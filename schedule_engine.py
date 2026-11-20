from datetime import datetime, timedelta


DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
CATEGORIES = ["Fokus", "Olahraga", "Mindfulness", "Membaca", "Lainnya"]


def _minutes(value, fallback):
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except (TypeError, ValueError):
        parsed = datetime.strptime(fallback, "%H:%M")
    return parsed.hour * 60 + parsed.minute


def _format_time(minutes):
    minutes %= 24 * 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def infer_category(habit_name):
    name = habit_name.casefold()
    if any(word in name for word in ("olahraga", "lari", "jalan", "gym", "latihan")):
        return "Olahraga"
    if any(word in name for word in ("meditasi", "napas", "mindful", "jurnal")):
        return "Mindfulness"
    if any(word in name for word in ("baca", "membaca", "buku")):
        return "Membaca"
    if any(word in name for word in ("belajar", "fokus", "prioritas", "kerja", "menulis")):
        return "Fokus"
    return "Lainnya"


def _preferred_windows(category, chronotype):
    windows = {
        "early": {
            "Fokus": [(8 * 60, 11 * 60 + 30)],
            "Olahraga": [(6 * 60, 9 * 60)],
            "Mindfulness": [(6 * 60, 9 * 60)],
            "Membaca": [(19 * 60, 21 * 60)],
            "Lainnya": [(9 * 60, 12 * 60)],
        },
        "intermediate": {
            "Fokus": [(9 * 60, 11 * 60 + 30), (15 * 60 + 30, 18 * 60)],
            "Olahraga": [(7 * 60, 10 * 60), (16 * 60, 19 * 60)],
            "Mindfulness": [(7 * 60, 9 * 60), (20 * 60, 21 * 60)],
            "Membaca": [(19 * 60, 21 * 60)],
            "Lainnya": [(9 * 60, 12 * 60), (16 * 60, 18 * 60)],
        },
        "late": {
            "Fokus": [(16 * 60, 21 * 60)],
            "Olahraga": [(16 * 60, 19 * 60)],
            "Mindfulness": [(9 * 60, 11 * 60), (20 * 60, 22 * 60)],
            "Membaca": [(20 * 60, 22 * 60)],
            "Lainnya": [(16 * 60, 20 * 60)],
        },
    }
    return windows.get(chronotype, windows["intermediate"]).get(category, windows["intermediate"]["Lainnya"])


def _window_distance(start, duration, window):
    left, right = window
    midpoint = start + duration / 2
    if left <= midpoint <= right:
        return 0
    return min(abs(midpoint - left), abs(midpoint - right))


def _parse_blocked_times(value):
    intervals = []
    invalid = []
    if not isinstance(value, str):
        return intervals, ["nilai waktu tidak tersedia"]
    for item in (value or "").split(","):
        entry = item.strip()
        if not entry:
            continue
        parts = entry.split("-")
        if len(parts) != 2:
            invalid.append(entry)
            continue
        try:
            start = _minutes(parts[0].strip(), "")
            end = _minutes(parts[1].strip(), "")
        except (TypeError, ValueError):
            invalid.append(entry)
            continue
        if end > start:
            intervals.append((start, end))
        else:
            invalid.append(entry)
    return intervals, invalid


def _overlaps(start, end, interval_start, interval_end):
    return start < interval_end and interval_start < end


def _adherence_for_habit(data, habit, days=14):
    active_days = set(habit.get("active_days", []))
    try:
        created_date = datetime.fromisoformat(habit.get("created_at", "")).date()
    except (TypeError, ValueError):
        created_date = datetime.now().date()
    due = 0
    done = 0.0
    observed = 0
    today = datetime.now().date()
    for offset in range(days):
        date = today - timedelta(days=offset)
        if date < created_date or DAY_NAMES[date.weekday()] not in active_days:
            continue
        due += 1
        for entry in data.get("activity_log", []):
            if entry.get("habit_id") == habit.get("id") and entry.get("date") == date.isoformat():
                resolution_scores = {
                    "completed": 1,
                    "partial": 0.5,
                    "shifted": 0.25,
                    "skipped": 0,
                }
                done += resolution_scores.get(
                    entry.get("resolution"),
                    1 if entry.get("completed") else 0,
                )
                observed += 1
                break
    return None if due == 0 or observed == 0 else done / due


def _time_feedback(data, habit, candidate_start, days=30):
    resolution_scores = {
        "completed": 1,
        "partial": 0.5,
        "shifted": 0.25,
        "skipped": 0,
    }
    relevant = []
    today = datetime.now().date()
    for entry in data.get("activity_log", []):
        if entry.get("habit_id") != habit.get("id") or not entry.get("planned_time"):
            continue
        try:
            entry_date = datetime.fromisoformat(entry.get("date", "")).date()
            entry_time = _minutes(entry.get("planned_time"), "")
        except (TypeError, ValueError):
            continue
        elapsed_days = (today - entry_date).days
        if not 0 <= elapsed_days <= days:
            continue
        if abs(candidate_start - entry_time) <= 45:
            relevant.append(
                resolution_scores.get(
                    entry.get("resolution"),
                    1 if entry.get("completed") else 0,
                )
            )
    if len(relevant) < 2:
        return None
    return sum(relevant) / len(relevant), len(relevant)


def build_schedule_suggestions(data):
    profile = data.get("profile", {})
    context = profile.get("schedule_context", {})
    wake = _minutes(profile.get("wake_time"), "07:00")
    sleep = _minutes(profile.get("sleep_time"), "22:30")
    work_start = _minutes(context.get("work_start"), "09:00")
    work_end = _minutes(context.get("work_end"), "17:00")
    dip_start = _minutes(context.get("energy_dip_start"), "13:00")
    dip_end = _minutes(context.get("energy_dip_end"), "15:00")
    configured_work_days = context.get("work_days", DAY_NAMES[:5])
    work_days = set(configured_work_days) if isinstance(configured_work_days, list) else set()
    blocked, invalid_blocks = _parse_blocked_times(context.get("blocked_times", ""))
    chronotype = context.get("chronotype", "intermediate")
    active_habits = [
        habit for habit in data.get("habits", []) if habit.get("active", True)
    ]

    warnings = []
    if invalid_blocks:
        warnings.append(
            "Rentang waktu yang diabaikan karena formatnya tidak valid: "
            + ", ".join(invalid_blocks)
            + ". Gunakan format HH:MM-HH:MM."
        )
    if sleep <= wake:
        warnings.append("Jam tidur harus lebih malam daripada jam bangun untuk membuat jadwal harian.")
        return {"suggestions": [], "warnings": warnings}

    reservations = {day: [] for day in DAY_NAMES}
    suggestions = []
    ordered_habits = sorted(
        active_habits,
        key=lambda habit: (-int(habit.get("duration", 20)), habit.get("name", "")),
    )

    for habit in ordered_habits:
        days = [day for day in DAY_NAMES if day in habit.get("active_days", [])]
        if not days:
            suggestions.append({
                "habit": habit,
                "time": None,
                "score": None,
                "reason": "Pilih minimal satu hari aktif sebelum menyusun jadwal.",
            })
            continue

        duration = max(5, int(habit.get("duration", 20)))
        category = habit.get("category") or infer_category(habit.get("name", ""))
        windows = _preferred_windows(category, chronotype)
        candidates = []
        latest_start = sleep - duration
        first_start = ((wake + 14) // 15) * 15
        for start in range(first_start, latest_start + 1, 15):
            end = start + duration
            valid = True
            for day in days:
                if day in work_days and _overlaps(start, end, work_start, work_end):
                    valid = False
                    break
                if any(_overlaps(start, end, left, right) for left, right in blocked):
                    valid = False
                    break
                if any(_overlaps(start - 15, end + 15, left, right) for left, right in reservations[day]):
                    valid = False
                    break
            if not valid:
                continue

            distance = min(_window_distance(start, duration, window) for window in windows)
            score = max(0, 100 - int(distance / 3))
            if _overlaps(start, end, dip_start, dip_end):
                score = max(0, score - 30)
            adherence = _adherence_for_habit(data, habit)
            if adherence is not None and adherence < 0.4:
                score = max(0, score - 8)
            elif adherence is not None and adherence >= 0.8:
                score = min(100, score + 3)
            time_feedback = _time_feedback(data, habit, start)
            if time_feedback:
                feedback_rate, _ = time_feedback
                score = max(0, min(100, score + round((feedback_rate - 0.5) * 24)))
            candidates.append((score, start, distance, adherence, time_feedback))

        if not candidates:
            suggestions.append({
                "habit": habit,
                "time": None,
                "score": None,
                "reason": "Tidak ada slot yang cocok dengan jam bangun/tidur, jam kerja, waktu yang dihindari, dan jeda antar-kebiasaan.",
            })
            continue

        score, start, distance, adherence, time_feedback = max(
            candidates, key=lambda candidate: (candidate[0], -candidate[1])
        )
        end = start + duration
        previous_start = _minutes(habit.get("time"), "08:00")
        previous_feedback = _time_feedback(data, habit, previous_start)
        for day in days:
            reservations[day].append((start, end))

        preferred_period = "rentang pilihan aktivitas"
        if distance == 0:
            preferred_period = "rentang yang cocok dengan preferensi waktu dan tipe aktivitas"
        reason_parts = [preferred_period]
        if _overlaps(start, end, dip_start, dip_end):
            reason_parts.append("waktu penurunan energi")
        if adherence is not None:
            reason_parts.append(f"konsistensi 14 hari {adherence:.0%}")
        if previous_feedback and start != previous_start:
            feedback_rate, feedback_count = previous_feedback
            reason_parts.append(
                f"dipindah dari {_format_time(previous_start)} setelah "
                f"{feedback_rate:.0%} hasil dari {feedback_count} catatan pada slot lama"
            )
        elif time_feedback:
            feedback_rate, feedback_count = time_feedback
            reason_parts.append(
                f"hasil pada jam sekitar ini {feedback_rate:.0%} dari {feedback_count} catatan"
            )
        suggestions.append({
            "habit": habit,
            "time": _format_time(start),
            "score": score,
            "reason": "; ".join(reason_parts),
        })

    return {"suggestions": suggestions, "warnings": warnings}
