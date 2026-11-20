import html
import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import streamlit as st
from auth import authenticate_account, create_account, get_account_data_path, initialize_auth_db
from schedule_engine import CATEGORIES, build_schedule_suggestions, infer_category

DATA_PATH = Path(__file__).with_name("data.json")
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEKDAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def default_data():
    return {
        "profile": {
            "onboarding_complete": False,
            "routine_status": "",
            "goal": "",
            "wake_time": "07:00",
            "sleep_time": "22:30",
            "habit_count_preference": "1-3 kebiasaan",
            "first_name": "",
            "schedule_context": {
                "chronotype": "intermediate",
                "work_start": "09:00",
                "work_end": "17:00",
                "work_days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
                "energy_dip_start": "13:00",
                "energy_dip_end": "15:00",
                "blocked_times": "",
                "anchor": "Tidak ada",
            },
        },
        "habits": [],
        "activity_log": [],
        "recommendations": [],
        "settings": {
            "theme": "dark",
            "default_active_days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
        },
    }


def validate_habit(habit):
    habit.setdefault("id", generate_habit_id())
    habit.setdefault("name", "Habit baru")
    habit.setdefault("time", "08:00")
    habit.setdefault("duration", 20)
    habit.setdefault("active_days", ["Mon", "Tue", "Wed", "Thu", "Fri"])
    habit.setdefault("active", True)
    try:
        datetime.fromisoformat(habit.get("created_at", ""))
    except (TypeError, ValueError):
        habit["created_at"] = datetime.now().date().isoformat()
    if habit.get("category") not in CATEGORIES:
        habit["category"] = infer_category(str(habit["name"]))
    if not isinstance(habit["active_days"], list):
        habit["active_days"] = []
    if not isinstance(habit["duration"], int):
        try:
            habit["duration"] = int(float(habit["duration"]))
        except (TypeError, ValueError):
            habit["duration"] = 20
    habit["duration"] = max(5, min(180, habit["duration"]))
    habit["name"] = str(habit["name"]).strip()[:60] or "Habit baru"
    if habit["time"] not in ["", None]:
        try:
            datetime.strptime(habit["time"], "%H:%M")
        except (TypeError, ValueError):
            habit["time"] = "08:00"
    return habit


def ensure_file_exists():
    path = get_data_path()
    if not path.exists():
        write_data(default_data())


def get_data_path():
    account = st.session_state.get("account")
    if account:
        return get_account_data_path(account["id"])
    return DATA_PATH


def read_data():
    ensure_file_exists()
    try:
        with get_data_path().open("r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError("Data file is not a valid object.")
        normalized = default_data()
        for key, default_value in normalized.items():
            if key not in raw:
                raw[key] = default_value
        if not isinstance(raw["profile"], dict):
            raw["profile"] = normalized["profile"]
        profile_defaults = normalized["profile"]
        for key, default_value in profile_defaults.items():
            raw["profile"].setdefault(key, default_value)
        if not isinstance(raw["profile"].get("schedule_context"), dict):
            raw["profile"]["schedule_context"] = profile_defaults["schedule_context"].copy()
        else:
            for key, default_value in profile_defaults["schedule_context"].items():
                raw["profile"]["schedule_context"].setdefault(key, default_value)
        if not isinstance(raw["habits"], list):
            raw["habits"] = []
        if not isinstance(raw["activity_log"], list):
            raw["activity_log"] = []
        if not isinstance(raw["recommendations"], list):
            raw["recommendations"] = []
        if not isinstance(raw["settings"], dict):
            raw["settings"] = normalized["settings"]
        raw["habits"] = [validate_habit(h) for h in raw["habits"]]
    except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
        st.error(f"Tidak dapat membaca data lokal akun ini. {exc}. Silakan periksa berkas data lokal atau reset data.")
        return default_data()

    changed, ambiguous_activity_count = repair_duplicate_ids(raw)
    if changed:
        write_data(raw)
        st.warning("ID duplikat pada kebiasaan atau rekomendasi telah diperbaiki.")
    if ambiguous_activity_count:
        st.warning(f"{ambiguous_activity_count} catatan aktivitas memiliki ID kebiasaan yang duplikat dan tidak dapat dipastikan pemiliknya; catatan tersebut tetap ditautkan ke kebiasaan pertama.")
    return raw


def write_data(data):
    data_path = get_data_path()
    data_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = data_path.with_suffix(".tmp")
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    with temp_path.open("w", encoding="utf-8") as f:
        f.write(payload)
    os.replace(temp_path, data_path)


def save_data(data):
    data["habits"] = [validate_habit(h) for h in data.get("habits", [])]
    write_data(data)


def get_today_iso():
    return datetime.now().date().isoformat()


def get_weekday_name(date_obj):
    return DAY_NAMES[date_obj.weekday()]


def day_name_to_index(value):
    try:
        return WEEKDAY_ORDER.index(value)
    except ValueError:
        return None


def encode_days(values):
    selected = []
    for day in WEEKDAY_ORDER:
        if day in values:
            selected.append(day)
    return selected


def habit_is_scheduled_for_date(habit, target_date):
    if not habit.get("active", True):
        return False
    weekday_name = get_weekday_name(target_date)
    return weekday_name in habit.get("active_days", [])


def habit_is_scheduled_today(habit):
    return habit_is_scheduled_for_date(habit, datetime.now().date())


def find_activity_entry(data, habit_id, target_date):
    for entry in data.get("activity_log", []):
        if entry.get("habit_id") == habit_id and entry.get("date") == target_date:
            return entry
    return None


def toggle_habit_completion(data, habit_id, complete):
    habit = next((h for h in data["habits"] if h["id"] == habit_id), None)
    if habit is None:
        raise ValueError("Kebiasaan untuk catatan ini tidak ditemukan.")
    target_date = get_today_iso()
    entry = find_activity_entry(data, habit_id, target_date)
    if not complete:
        if entry:
            data["activity_log"].remove(entry)
            save_data(data)
        return
    record_habit_outcome(data, habit_id, "completed")


def record_habit_outcome(data, habit_id, outcome):
    allowed_outcomes = {"completed", "partial", "shifted", "skipped"}
    if outcome not in allowed_outcomes:
        raise ValueError(f"Hasil kebiasaan tidak dikenal: {outcome}")
    habit = next((h for h in data["habits"] if h["id"] == habit_id), None)
    if habit is None:
        raise ValueError("Kebiasaan untuk catatan ini tidak ditemukan.")
    target_date = get_today_iso()
    now = datetime.now()
    entry = find_activity_entry(data, habit_id, target_date)
    if entry is None:
        entry = {"habit_id": habit_id, "date": target_date}
        data["activity_log"].append(entry)
    entry.update({
        "completed": outcome == "completed",
        "resolution": outcome,
        "completed_at": now.strftime("%H:%M") if outcome == "completed" else "",
        "recorded_at": now.isoformat(timespec="minutes"),
        "planned_time": habit.get("time", "08:00"),
        "duration": habit.get("duration", 20),
    })
    save_data(data)


def generate_habit_id():
    return f"habit-{uuid.uuid4().hex}"


def generate_recommendation_id():
    return f"rec-{uuid.uuid4().hex}"


def repair_duplicate_ids(data):
    changed = False
    habit_groups = {}
    seen_habit_ids = set()
    reserved_habit_ids = {habit["id"] for habit in data["habits"]}

    for habit in data["habits"]:
        original_id = habit["id"]
        if original_id in seen_habit_ids:
            habit["id"] = generate_habit_id()
            while habit["id"] in reserved_habit_ids or habit["id"] in seen_habit_ids:
                habit["id"] = generate_habit_id()
            changed = True
        seen_habit_ids.add(habit["id"])
        habit_groups.setdefault(original_id, []).append((habit["id"], habit["name"]))

    ambiguous_activity_count = 0
    for entry in data["activity_log"]:
        candidates = habit_groups.get(entry.get("habit_id"), [])
        if len(candidates) > 1:
            ambiguous_activity_count += 1

    for recommendation in data["recommendations"]:
        candidates = habit_groups.get(recommendation.get("habit_id"), [])
        if len(candidates) > 1:
            text = str(recommendation.get("text", "")).casefold()
            matches = [
                (habit_id, name)
                for habit_id, name in candidates
                if name and text.startswith(name.casefold())
            ]
            if len(matches) == 1 and recommendation["habit_id"] != matches[0][0]:
                recommendation["habit_id"] = matches[0][0]
                changed = True

    seen_recommendation_ids = set()
    reserved_recommendation_ids = {
        recommendation.get("id")
        for recommendation in data["recommendations"]
        if recommendation.get("id")
    }
    for recommendation in data["recommendations"]:
        recommendation_id = recommendation.get("id")
        if not recommendation_id or recommendation_id in seen_recommendation_ids:
            recommendation["id"] = generate_recommendation_id()
            while recommendation["id"] in reserved_recommendation_ids or recommendation["id"] in seen_recommendation_ids:
                recommendation["id"] = generate_recommendation_id()
            changed = True
        seen_recommendation_ids.add(recommendation["id"])

    return changed, ambiguous_activity_count


def calculate_success_rate(data, habit_id, days=7):
    habit = next((h for h in data["habits"] if h["id"] == habit_id), None)
    if habit is None:
        return None
    try:
        created_date = datetime.fromisoformat(habit.get("created_at", "")).date()
    except (TypeError, ValueError):
        created_date = datetime.now().date()
    opportunities = 0
    completed = 0
    end_date = datetime.now().date()
    for offset in range(days):
        current_date = end_date - timedelta(days=offset)
        if current_date >= created_date and habit_is_scheduled_for_date(habit, current_date):
            opportunities += 1
            entry = find_activity_entry(data, habit_id, current_date.isoformat())
            if entry:
                resolution_scores = {
                    "completed": 1,
                    "partial": 0.5,
                    "shifted": 0.25,
                    "skipped": 0,
                }
                completed += resolution_scores.get(
                    entry.get("resolution"),
                    1 if entry.get("completed") else 0,
                )
    if opportunities == 0:
        return None
    return completed / opportunities


def recommendation_status_for_rate(rate):
    if rate is None:
        return "Data belum cukup"
    if rate >= 0.75:
        return "Tinggi"
    if rate >= 0.45:
        return "Sedang"
    return "Rendah"


def detect_conflicts(data):
    conflicts = []
    day_map = {}
    for habit in data.get("habits", []):
        if not habit.get("active", True):
            continue
        for day in habit.get("active_days", []):
            day_map.setdefault(day, []).append(habit)
    for day_name, habits in day_map.items():
        seen = {}
        for habit in habits:
            time_key = str(habit.get("time", "08:00"))
            seen.setdefault(time_key, []).append(habit["name"])
        for time_key, names in seen.items():
            if len(names) > 1:
                conflicts.append({"day": day_name, "time": time_key, "habits": names})
    return conflicts


def analyze_habit_pattern(data, habit_id):
    habit = next((h for h in data["habits"] if h["id"] == habit_id), None)
    if habit is None:
        return None
    rates = []
    for offset in range(7):
        current_date = datetime.now().date() - timedelta(days=offset)
        if habit_is_scheduled_for_date(habit, current_date):
            entry = find_activity_entry(data, habit_id, current_date.isoformat())
            rates.append(1 if entry and entry.get("completed") else 0)
    if not rates:
        return None
    recent = sum(rates) / len(rates)
    if recent >= 0.7:
        return "Kamu cukup konsisten dengan kebiasaan ini. Pertahankan pola yang sudah berjalan."
    if recent >= 0.4:
        return "Kebiasaan ini masih perlu pengamatan. Coba amati apakah waktunya atau durasinya terlalu berat."
    return "Kebiasaan ini belum konsisten. Coba sesuaikan waktu atau durasi agar lebih realistis."


def generate_recommendations(data):
    existing = data.get("recommendations", [])
    active_habits = [h for h in data.get("habits", []) if h.get("active", True)]
    generated = []
    for habit in active_habits:
        rate = calculate_success_rate(data, habit["id"], 7)
        if rate is None:
            continue
        if rate >= 0.75:
            text = f"Kamu cukup konsisten dengan {habit['name']}. Pertahankan jadwal ini di minggu depan."
        elif rate < 0.4:
            text = f"{habit['name']} sering terlewat. Coba ujicoba durasi lebih pendek atau pindah ke waktu yang lebih nyaman."
        else:
            text = f"{habit['name']} masih perlu pengamatan. Pertimbangkan evaluasi waktu dan durasi untuk menjaga momentum."
        existing_texts = [entry.get("text") for entry in existing if entry.get("status") != "dismissed"]
        if text not in existing_texts:
            generated.append({
                "id": generate_recommendation_id(),
                "habit_id": habit["id"],
                "text": text,
                "created_at": datetime.now().date().isoformat(),
                "status": "pending",
            })

    conflicts = detect_conflicts(data)
    for conflict in conflicts:
        text = f"Ada beberapa kebiasaan pada {conflict['day']} pukul {conflict['time']}. Coba review jadwalmu agar tidak tumpang tindih."
        existing_texts = [entry.get("text") for entry in existing if entry.get("status") != "dismissed"]
        if text not in existing_texts:
            generated.append({
                "id": generate_recommendation_id(),
                "habit_id": None,
                "text": text,
                "created_at": datetime.now().date().isoformat(),
                "status": "pending",
            })

    if not generated and not active_habits:
        generated.append({
            "id": generate_recommendation_id(),
            "habit_id": None,
            "text": "Saat ini belum ada kebiasaan aktif. Mulai dengan 1–2 kebiasaan yang paling sesuai dengan rutinitasmu.",
            "created_at": datetime.now().date().isoformat(),
            "status": "pending",
        })

    data["recommendations"] = existing + generated
    data["recommendations"] = data["recommendations"][-20:]
    return data["recommendations"]


def update_recommendation_status(data, recommendation_id, action):
    for rec in data.get("recommendations", []):
        if rec.get("id") == recommendation_id:
            rec["status"] = action
            rec["updated_at"] = datetime.now().isoformat()
            break
    save_data(data)


def compute_daily_completion(data):
    habits = [h for h in data["habits"] if h.get("active", True)]
    today = get_today_iso()
    scheduled_today = [h for h in habits if habit_is_scheduled_for_date(h, datetime.now().date())]
    completed_today = 0
    for habit in scheduled_today:
        entry = find_activity_entry(data, habit["id"], today)
        if entry and entry.get("completed"):
            completed_today += 1
    return scheduled_today, completed_today


def compute_consistency(data):
    metrics = []
    for habit in data.get("habits", []):
        if not habit.get("active", True):
            continue
        rate = calculate_success_rate(data, habit["id"], 7)
        if rate is None:
            score = "Belum cukup data"
        elif rate >= 0.75:
            score = "Tinggi"
        elif rate >= 0.45:
            score = "Cukup"
        else:
            score = "Perlu penyesuaian"
        metrics.append({"habit": habit["name"], "score": score, "rate": rate})
    return metrics


def compute_streak(data, habit_id):
    habit = next((h for h in data["habits"] if h["id"] == habit_id), None)
    if habit is None:
        return 0
    streak = 0
    current = datetime.now().date()
    while True:
        if not habit_is_scheduled_for_date(habit, current):
            current -= timedelta(days=1)
            continue
        entry = find_activity_entry(data, habit_id, current.isoformat())
        if entry and entry.get("completed"):
            streak += 1
            current -= timedelta(days=1)
        else:
            break
    return streak


def create_default_habits(profile):
    habits = []
    wake_time = profile.get("wake_time", "07:00")
    sleep_time = profile.get("sleep_time", "22:30")
    routine_status = (profile.get("routine_status") or "").lower()
    goal = (profile.get("goal") or "").lower()
    count_map = {
        "1-3 kebiasaan": 2,
        "4-5 kebiasaan": 4,
        "menentukan sendiri": 3,
    }
    requested = count_map.get(profile.get("habit_count_preference"), 2)
    if routine_status == "sudah cukup teratur":
        requested = max(2, min(requested, 4))
    elif routine_status == "ingin mulai membangun rutinitas dari awal":
        requested = max(1, min(requested, 3))

    base_templates = [
        {"name": "Menyusun prioritas hari", "time": wake_time, "duration": 10, "days": ["Mon", "Tue", "Wed", "Thu", "Fri"]},
        {"name": "Olahraga ringkas", "time": "07:15", "duration": 15, "days": ["Mon", "Wed", "Fri"]},
        {"name": "Belajar fokus", "time": "20:30", "duration": 25, "days": ["Mon", "Tue", "Wed", "Thu", "Fri"]},
        {"name": "Membaca 10 menit", "time": "21:00", "duration": 10, "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]},
        {"name": "Tidur tepat waktu", "time": sleep_time, "duration": 5, "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]},
    ]
    selected = []
    if "olahraga" in goal:
        selected = [0, 1, 3]
    elif "tidur" in goal:
        selected = [0, 4, 3]
    elif "belajar" in goal or "bekerja" in goal:
        selected = [0, 2, 3]
    elif "waktu" in goal:
        selected = [0, 2, 3]
    else:
        selected = [0, 2, 3]

    if not selected:
        selected = [0, 2, 3]

    for idx in selected[:requested]:
        item = base_templates[idx]
        habits.append({
            "id": generate_habit_id(),
            "name": item["name"],
            "time": item["time"],
            "duration": item["duration"],
            "active_days": item["days"],
            "active": True,
        })

    if not habits:
        habits.append({
            "id": generate_habit_id(),
            "name": "Habit inti",
            "time": wake_time,
            "duration": 15,
            "active_days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
            "active": True,
        })

    return habits


def show_onboarding(data):
    st.title("Golden Time")
    st.subheader("Mulai dengan rutinitas yang realistis")
    st.caption("Selangkah lagi untuk menyesuaikan jadwalmu dan memulai dengan kebiasaan yang pas.")

    with st.form("onboarding_form"):
        routine_status = st.selectbox(
            "A. Bagaimana kondisi rutinitasmu?",
            ["Sudah cukup teratur", "Kadang teratur, kadang tidak", "Ingin mulai membangun rutinitas dari awal"],
            index=0,
        )
        goal = st.selectbox(
            "B. Apa tujuan utamamu?",
            ["Mengatur waktu", "Memperbaiki pola tidur", "Lebih konsisten belajar atau bekerja", "Membiasakan olahraga", "Menyeimbangkan rutinitas"],
            index=0,
        )
        col1, col2 = st.columns(2)
        wake_time = col1.time_input("C. Kapan biasanya kamu bangun?", value=datetime.strptime("07:00", "%H:%M").time())
        sleep_time = col2.time_input("D. Kapan biasanya kamu tidur?", value=datetime.strptime("22:30", "%H:%M").time())
        habit_count = st.selectbox(
            "E. Berapa banyak kebiasaan yang ingin dimulai?",
            ["1-3 kebiasaan", "4-5 kebiasaan", "Menentukan sendiri"],
            index=0,
        )
        submit = st.form_submit_button("Selesai")

    if submit:
        data["profile"]["onboarding_complete"] = True
        data["profile"]["routine_status"] = routine_status
        data["profile"]["goal"] = goal
        data["profile"]["wake_time"] = wake_time.strftime("%H:%M")
        data["profile"]["sleep_time"] = sleep_time.strftime("%H:%M")
        data["profile"]["habit_count_preference"] = habit_count
        if not data["habits"]:
            data["habits"] = create_default_habits(data["profile"])
        save_data(data)
        st.success("Onboarding selesai. Dashboard siap menampilkan rutinitasmu.")
        st.rerun()


def render_home(data):
    profile = data["profile"]
    context = profile.get("schedule_context", {})
    hour = datetime.now().hour
    greeting = "Selamat pagi" if hour < 12 else ("Selamat siang" if hour < 18 else "Selamat malam")
    name = html.escape(profile.get("first_name") or st.session_state["account"]["display_name"])
    today = datetime.now().date()
    scheduled_today, completed_today = compute_daily_completion(data)
    plan = build_schedule_suggestions(data)

    st.markdown(
        f"""
        <section class="hero-panel">
            <div class="eyebrow">GOLDEN TIME · PERENCANA JADWAL PERSONAL</div>
            <h1>{greeting}, {name}.</h1>
            <p>Jadwal yang mempertimbangkan waktumu, bukan hanya daftar kebiasaan.</p>
            <div class="hero-meta">MESIN ATURAN TRANSPARAN <span>•</span> ZONA WAKTU PERANGKAT</div>
        </section>
        """,
        unsafe_allow_html=True,
    )

    main_col, side_col = st.columns([1.7, 1])
    with main_col:
        st.markdown("### Jadwal hari ini")
        st.caption(f"{today.strftime('%A, %d %B %Y')} · {len(scheduled_today)} kebiasaan terjadwal")
        if not scheduled_today:
            st.info("Hari ini tidak memiliki kebiasaan aktif. Atur hari aktif di Kebiasaan.")
        for habit in sorted(scheduled_today, key=lambda item: item.get("time", "")):
            entry = find_activity_entry(data, habit["id"], get_today_iso())
            is_done = bool(entry and entry.get("completed"))
            with st.container(border=True):
                time_col, detail_col, action_col = st.columns([0.8, 2.4, 1])
                with time_col:
                    st.markdown(f"<div class='time-chip'>{habit['time']}</div>", unsafe_allow_html=True)
                with detail_col:
                    st.markdown(f"**{habit['name']}**")
                    st.caption(f"{habit.get('category', infer_category(habit['name']))} · {habit['duration']} menit")
                with action_col:
                    if is_done:
                        st.success("Selesai")
                        if st.button("Batalkan", key=f"cancel_{habit['id']}"):
                            toggle_habit_completion(data, habit["id"], False)
                            st.rerun()
                    else:
                        if entry:
                            resolution_labels = {
                                "completed": "Selesai penuh",
                                "partial": "Sebagian selesai",
                                "shifted": "Dipindahkan",
                                "skipped": "Dilewati",
                            }
                            st.caption(resolution_labels.get(entry.get("resolution", ""), "Tercatat"))
                        with st.expander("Catat hasil aktual"):
                            outcomes = ["completed", "partial", "shifted", "skipped"]
                            labels = {
                                "completed": "Selesai penuh",
                                "partial": "Sebagian selesai",
                                "shifted": "Dipindahkan ke waktu lain",
                                "skipped": "Dilewati hari ini",
                            }
                            selected_outcome = st.selectbox(
                                "Hasil",
                                outcomes,
                                format_func=lambda value: labels[value],
                                key=f"outcome_{habit['id']}",
                            )
                            if st.button("Simpan hasil", key=f"save_outcome_{habit['id']}", type="primary"):
                                record_habit_outcome(data, habit["id"], selected_outcome)
                                st.rerun()

    with side_col:
        st.markdown("### Ritme hari ini")
        completion_rate = 0 if not scheduled_today else completed_today / len(scheduled_today)
        st.metric("Penyelesaian hari ini", f"{completion_rate:.0%}", f"{completed_today}/{len(scheduled_today)} selesai")
        consistency = [item for item in compute_consistency(data) if item["rate"] is not None]
        if consistency:
            average = sum(item["rate"] for item in consistency) / len(consistency)
            st.metric("Konsistensi 7 hari", f"{average:.0%}")
        else:
            st.metric("Konsistensi 7 hari", "Belum ada data")
        st.markdown(
            f"""
            <div class="insight-card">
              <div class="eyebrow">KONTEKS YANG DIPAKAI</div>
              <p><b>Tipe waktu:</b> {context.get('chronotype', 'intermediate').title()}</p>
              <p><b>Jam aktif:</b> {profile.get('wake_time', '07:00')} – {profile.get('sleep_time', '22:30')}</p>
              <p><b>Jam kerja:</b> {context.get('work_start', '09:00')} – {context.get('work_end', '17:00')}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("---")
    st.markdown("### Usulan penempatan jadwal")
    st.caption("Skor adalah kecocokan terhadap aturan dan preferensimu, bukan probabilitas keberhasilan.")
    if not data["habits"]:
        st.info("Tambahkan kebiasaan untuk membuat usulan jadwal yang mempertimbangkan konteks harianmu.")
        return
    for warning in plan["warnings"]:
        st.warning(warning)
    proposed = [item for item in plan["suggestions"] if item["time"]]
    impossible = [item for item in plan["suggestions"] if not item["time"]]
    if proposed:
        for item in proposed:
            habit = item["habit"]
            with st.container(border=True):
                suggestion_col, name_col, score_col = st.columns([0.8, 2.5, 1])
                suggestion_col.markdown(f"<div class='time-chip'>{item['time']}</div>", unsafe_allow_html=True)
                name_col.markdown(f"**{habit['name']}** · {habit['duration']} menit")
                name_col.caption(f"{', '.join(habit.get('active_days', []))} · {item['reason']}")
                score_col.metric("Kecocokan", f"{item['score']}/100")
        if st.button("Terapkan usulan jadwal", type="primary", key="apply_schedule"):
            for item in proposed:
                item["habit"]["time"] = item["time"]
            save_data(data)
            st.success("Jadwal kebiasaan telah diperbarui.")
            st.rerun()
    for item in impossible:
        st.warning(f"{item['habit']['name']}: {item['reason']}")
    st.caption("Perhitungan memakai jam bangun/tidur, hari kerja, jam kerja, waktu yang dihindari, tipe aktivitas, preferensi waktu, dan konsistensi 14 hari bila tersedia.")


def render_profiler(data):
    profile = data["profile"]
    context = profile.get("schedule_context", {})
    st.title("Profiler jadwal")
    st.caption("Berikan batasan dan kebiasaan harian agar mesin aturan dapat mencari slot yang realistis.")

    with st.form("schedule_profiler"):
        st.markdown("#### A · Ritme dan energi")
        col1, col2, col3 = st.columns(3)
        wake_time = col1.time_input(
            "Jam bangun",
            value=datetime.strptime(profile.get("wake_time", "07:00"), "%H:%M").time(),
        )
        sleep_time = col2.time_input(
            "Jam tidur",
            value=datetime.strptime(profile.get("sleep_time", "22:30"), "%H:%M").time(),
        )
        chronotype_options = {
            "early": "Lebih fokus pagi",
            "intermediate": "Seimbang pagi dan sore",
            "late": "Lebih fokus sore/malam",
        }
        chronotype = col3.selectbox(
            "Waktu fokus pilihan",
            list(chronotype_options),
            format_func=lambda value: chronotype_options[value],
            index=list(chronotype_options).index(context.get("chronotype", "intermediate")),
        )

        st.markdown("#### B · Kewajiban dan waktu yang dihindari")
        col1, col2, col3 = st.columns(3)
        work_start = col1.time_input(
            "Mulai jam kerja/kuliah",
            value=datetime.strptime(context.get("work_start", "09:00"), "%H:%M").time(),
        )
        work_end = col2.time_input(
            "Selesai jam kerja/kuliah",
            value=datetime.strptime(context.get("work_end", "17:00"), "%H:%M").time(),
        )
        work_days = col3.multiselect(
            "Hari kerja/kuliah",
            WEEKDAY_ORDER,
            default=context.get("work_days", WEEKDAY_ORDER[:5]),
        )
        col1, col2 = st.columns(2)
        energy_dip_start = col1.time_input(
            "Mulai waktu energi rendah",
            value=datetime.strptime(context.get("energy_dip_start", "13:00"), "%H:%M").time(),
        )
        energy_dip_end = col2.time_input(
            "Akhir waktu energi rendah",
            value=datetime.strptime(context.get("energy_dip_end", "15:00"), "%H:%M").time(),
        )
        blocked_times = st.text_input(
            "Waktu lain yang tidak tersedia",
            value=context.get("blocked_times", ""),
            placeholder="Contoh: 12:00-13:00, 17:30-18:00",
            help="Masukkan rentang jam dipisahkan koma. Rentang ini dihindari pada setiap hari.",
        )
        first_name = st.text_input("Nama panggilan", value=profile.get("first_name", ""))
        save_context = st.form_submit_button("Simpan konteks jadwal", type="primary")

    if save_context:
        if sleep_time <= wake_time:
            st.error("Untuk saat ini pilih jam tidur yang lebih malam daripada jam bangun.")
        elif work_end <= work_start:
            st.error("Jam selesai kerja/kuliah harus lebih malam daripada jam mulai.")
        elif energy_dip_end <= energy_dip_start:
            st.error("Waktu energi rendah harus memiliki jam akhir setelah jam mulainya.")
        else:
            profile["wake_time"] = wake_time.strftime("%H:%M")
            profile["sleep_time"] = sleep_time.strftime("%H:%M")
            profile["first_name"] = first_name.strip()
            profile["onboarding_complete"] = True
            profile["schedule_context"] = {
                "chronotype": chronotype,
                "work_start": work_start.strftime("%H:%M"),
                "work_end": work_end.strftime("%H:%M"),
                "work_days": work_days,
                "energy_dip_start": energy_dip_start.strftime("%H:%M"),
                "energy_dip_end": energy_dip_end.strftime("%H:%M"),
                "blocked_times": blocked_times.strip(),
            }
            save_data(data)
            st.success("Konteks jadwal tersimpan. Mesin aturan akan memakai informasi ini saat menyusun usulan.")
            st.rerun()

    st.markdown("---")
    st.markdown("#### Bagaimana jadwal disusun")
    st.write(
        "Mesin mencari slot 15 menit di antara jam bangun dan tidur, membuang jam kerja "
        "serta rentang yang kamu tandai tidak tersedia, lalu memberi nilai kecocokan "
        "berdasarkan jenis kebiasaan, preferensi waktu, jeda antar-kebiasaan, dan riwayat "
        "penyelesaian 14 hari jika datanya tersedia. Jadwal hanya berubah setelah kamu "
        "menekan **Terapkan usulan jadwal**."
    )


def render_habits(data):
    st.title("Habits")
    st.caption("Kelola kebiasaan, jadwal, dan durasi yang paling sesuai dengan rutinitasmu.")

    if not data["habits"]:
        st.info("Belum ada kebiasaan. Tambahkan kebiasaan baru untuk memulai.")

    add_col, _, _ = st.columns([2, 1, 1])
    with add_col:
        if st.button("Tambah kebiasaan baru"):
            st.session_state["show_add_habit"] = True
            st.rerun()

    if st.session_state.get("show_add_habit"):
        with st.form("new_habit_form"):
            name = st.text_input("Nama kebiasaan")
            category = st.selectbox("Jenis aktivitas", CATEGORIES)
            time_value = st.time_input("Jam pelaksanaan", value=datetime.strptime("08:00", "%H:%M").time())
            duration = st.number_input("Durasi (menit)", min_value=5, max_value=180, value=20, step=5)
            active_days = st.multiselect("Hari aktif", WEEKDAY_ORDER, default=WEEKDAY_ORDER[:5])
            active = st.checkbox("Status aktif", value=True)
            if st.form_submit_button("Simpan kebiasaan"):
                if not name.strip():
                    st.warning("Nama kebiasaan wajib diisi.")
                else:
                    new_habit = {
                        "id": generate_habit_id(),
                        "name": name.strip(),
                        "category": category,
                        "time": time_value.strftime("%H:%M"),
                        "duration": int(duration),
                        "active_days": active_days,
                        "active": active,
                    }
                    conflicts = detect_conflicts({**data, "habits": data["habits"] + [new_habit]})
                    if conflicts:
                        st.warning("Jadwal ini berpotensi berbenturan dengan kebiasaan lain. Tetap lanjutkan jika menurutmu masih nyaman.")
                    data["habits"].append(new_habit)
                    save_data(data)
                    st.session_state["show_add_habit"] = False
                    st.success("Kebiasaan baru berhasil ditambahkan.")
                    st.rerun()

    st.markdown("---")
    for habit in data["habits"]:
        st.subheader(habit["name"])
        with st.expander(f"Detail · {habit['name']}", expanded=False):
            with st.form(f"edit_{habit['id']}"):
                name_input = st.text_input("Nama", value=habit["name"], key=f"name_{habit['id']}")
                category_input = st.selectbox(
                    "Jenis aktivitas",
                    CATEGORIES,
                    index=CATEGORIES.index(habit.get("category", infer_category(habit["name"]))),
                    key=f"category_{habit['id']}",
                )
                time_input = st.time_input("Jam", value=datetime.strptime(habit.get("time", "08:00"), "%H:%M").time(), key=f"time_{habit['id']}")
                duration_input = st.number_input("Durasi (menit)", min_value=5, max_value=180, value=int(habit.get("duration", 20)), step=5, key=f"dur_{habit['id']}")
                days_input = st.multiselect("Hari aktif", WEEKDAY_ORDER, default=habit.get("active_days", ["Mon", "Tue", "Wed", "Thu", "Fri"]), key=f"days_{habit['id']}")
                active_input = st.checkbox("Aktif", value=habit.get("active", True), key=f"active_{habit['id']}")
                c1, c2 = st.columns(2)
                with c1:
                    save_btn = st.form_submit_button("Simpan perubahan")
                with c2:
                    delete_btn = st.form_submit_button("Hapus")

                if save_btn:
                    habit["name"] = name_input.strip() or habit["name"]
                    habit["category"] = category_input
                    habit["time"] = time_input.strftime("%H:%M")
                    habit["duration"] = int(duration_input)
                    habit["active_days"] = encode_days(days_input)
                    habit["active"] = active_input
                    save_data(data)
                    st.success("Kebiasaan berhasil diperbarui.")
                    st.rerun()
                if delete_btn:
                    confirmation = st.checkbox("Konfirmasi hapus kebiasaan", key=f"confirm_{habit['id']}")
                    if confirmation:
                        data["habits"] = [h for h in data["habits"] if h["id"] != habit["id"]]
                        data["activity_log"] = [entry for entry in data["activity_log"] if entry.get("habit_id") != habit["id"]]
                        data["recommendations"] = [rec for rec in data.get("recommendations", []) if rec.get("habit_id") != habit["id"]]
                        save_data(data)
                        st.success("Kebiasaan berhasil dihapus.")
                        st.rerun()

        streak = compute_streak(data, habit["id"])
        rate = calculate_success_rate(data, habit["id"], 7)
        rate_text = "Belum cukup data" if rate is None else f"{rate:.0%}"
        st.write(f"Streak: {streak} hari | Tingkat keberhasilan 7 hari: {rate_text}")
        st.markdown("---")


def render_insights(data):
    st.title("Insights")
    st.caption("Statistik yang terlihat, sederhana, dan relevan dengan perilaku harianmu.")

    total_habits = len([h for h in data["habits"] if h.get("active", True)])
    if total_habits == 0:
        st.info("Statistik akan muncul setelah kamu menambahkan kebiasaan aktif.")
        return

    last_7_days = []
    for offset in range(7):
        day = datetime.now().date() - timedelta(days=offset)
        scheduled = [h for h in data["habits"] if h.get("active", True) and habit_is_scheduled_for_date(h, day)]
        completed = 0
        for habit in scheduled:
            entry = find_activity_entry(data, habit["id"], day.isoformat())
            if entry and entry.get("completed"):
                completed += 1
        last_7_days.append({"day": day.strftime("%a"), "completed": completed, "scheduled": len(scheduled)})

    st.subheader("Tingkat penyelesaian 7 hari terakhir")
    for item in reversed(last_7_days):
        pct = 0 if item["scheduled"] == 0 else (item["completed"] / item["scheduled"]) * 100
        st.write(f"{item['day']}: {item['completed']}/{item['scheduled']} ({pct:.0f}%)")

    st.markdown("---")
    best = max(compute_consistency(data), key=lambda item: item["rate"] if item["rate"] is not None else -1)
    st.write(f"Kebiasaan dengan konsistensi tertinggi: **{best['habit']}** ({recommendation_status_for_rate(best['rate'])})")

    low = [item for item in compute_consistency(data) if item["rate"] is not None and item["rate"] < 0.5]
    if low:
        st.write("Kebiasaan yang membutuhkan penyesuaian:")
        for item in low:
            st.write(f"- {item['habit']} ({item['rate']:.0%})")
    else:
        st.write("Semua kebiasaan yang aktif sedang dalam kondisi cukup baik.")

    st.markdown("---")
    st.subheader("Penyesuaian yang bisa dicoba")
    if low:
        for item in low:
            habit = next((h for h in data["habits"] if h["name"] == item["habit"]), None)
            if habit:
                st.info(
                    f"{habit['name']} selesai {item['rate']:.0%} dari jadwal selama 7 hari. "
                    "Pertimbangkan mengurangi durasi atau memindahkan waktunya, lalu amati hasil minggu berikutnya."
                )
    else:
        st.info("Belum ada pola konsistensi rendah yang membutuhkan penyesuaian.")


def render_auth_page():
    if "auth_mode" not in st.session_state:
        st.session_state["auth_mode"] = "Masuk"
    st.markdown(
        """
        <div class="auth-shell">
          <div class="auth-mark">◷</div>
          <div class="eyebrow">CHRONOS · PERSONAL TIME PLANNER</div>
          <h1>GOLDEN TIME</h1>
          <p class="auth-subtitle">Susun kebiasaan yang cocok dengan ritme hidupmu.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    _, center, _ = st.columns([1, 1.25, 1])
    with center:
        mode = st.radio(
            "Akses",
            ["Masuk", "Buat akun"],
            horizontal=True,
            key="auth_mode",
            label_visibility="collapsed",
        )
        if mode == "Masuk":
            with st.form("login_form"):
                email = st.text_input("Email")
                password = st.text_input("Kata sandi", type="password")
                submitted = st.form_submit_button("MASUK KE GOLDEN TIME", type="primary", use_container_width=True)
            if submitted:
                account = authenticate_account(email, password)
                if account is None:
                    st.error("Email atau kata sandi tidak cocok.")
                else:
                    st.session_state["account"] = account
                    st.session_state["active_page"] = "Dashboard"
                    st.rerun()
            if st.button("Lupa kata sandi?", key="forgot_password"):
                st.info(
                    "Akun dan data tersimpan hanya di perangkat ini. Belum ada layanan "
                    "pemulihan akun online; pastikan kata sandi lokalmu tersimpan aman."
                )
        else:
            with st.form("register_form"):
                display_name = st.text_input("Nama panggilan")
                email = st.text_input("Email")
                password = st.text_input("Buat kata sandi", type="password")
                confirm_password = st.text_input("Ulangi kata sandi", type="password")
                submitted = st.form_submit_button("BUAT AKUN LOKAL", type="primary", use_container_width=True)
            if submitted:
                if password != confirm_password:
                    st.error("Konfirmasi kata sandi belum sama.")
                else:
                    account, error = create_account(email, password, display_name)
                    if error:
                        st.error(error)
                    else:
                        st.session_state["account"] = account
                        st.session_state["active_page"] = "Profiler"
                        st.success("Akun lokal berhasil dibuat. Silakan atur konteks jadwalmu.")
                        st.rerun()
        st.caption("Akun lokal di perangkat ini · kata sandi disimpan sebagai hash · tidak ada sinkronisasi cloud.")
        with st.expander("Petunjuk akses dan pemulihan"):
            st.markdown(
                "- Pilih **Buat akun** untuk membuat akun pertama di perangkat ini.\n"
                "- Kata sandi minimal 10 karakter; akun hanya tersedia di komputer yang menjalankan aplikasi.\n"
                "- Jika ada `data.json` lama, data itu disalin ke akun lokal pertama dan berkas lama tetap disimpan.\n"
                "- Belum ada sinkronisasi online atau reset kata sandi otomatis. Simpan kata sandi dengan aman."
            )


def render_styles():
    st.markdown(
        """
        <style>
        :root { --bg:#131313; --panel:#1c1b1b; --panel2:#201f1f; --gold:#f2ca50; --gold2:#d4af37; --primary-color:#d4af37; --text:#e5e2e1; --muted:#b7ad99; }
        .stApp { background: radial-gradient(ellipse at 70% 0%, rgba(212,175,55,.10), transparent 38%), var(--bg); color:var(--text); font-family:'Segoe UI',sans-serif; }
        .block-container { max-width:1320px; padding-top:1.6rem; padding-bottom:3rem; }
        h1,h2,h3 { font-family:Georgia,serif !important; color:var(--text); }
        h1 { letter-spacing:-.03em; }
        [data-testid="stHeader"] { background:transparent; }
        [data-testid="stSidebar"] { background:#0e0e0e; }
        .stButton > button { border:1px solid rgba(242,202,80,.25); border-radius:7px; background:var(--gold); color:#17130a; font-weight:700; min-height:2.55rem; }
        .stButton > button:hover { background:#ffdf75; color:#17130a; border-color:var(--gold); }
        [data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"], .stButton > button[kind="primary"] { background:#d4af37 !important; color:#17130a !important; border-color:#d4af37 !important; }
        [data-testid="stBaseButton-primary"]:hover, [data-testid="stBaseButton-primaryFormSubmit"]:hover, .stButton > button[kind="primary"]:hover { background:#f2ca50 !important; color:#17130a !important; }
        button[kind="secondary"] { background:#242323; color:var(--text); border-color:#353534; }
        [data-testid="stMetric"] { background:#1b1a1a; border:1px solid #302d25; padding:14px 16px; border-radius:9px; }
        [data-testid="stMetricValue"] { color:var(--gold); }
        [data-testid="stVerticalBlockBorderWrapper"] { background:rgba(28,27,27,.75); border-color:#34312a !important; border-radius:9px; }
        .hero-panel { background:linear-gradient(115deg,rgba(212,175,55,.11),rgba(28,27,27,.94) 56%),#1c1b1b; border:1px solid #383326; border-radius:12px; padding:28px 34px; margin:4px 0 25px; box-shadow:0 18px 50px rgba(0,0,0,.2); }
        .hero-panel h1 { font-size:clamp(2rem,4vw,3.35rem); margin:.35rem 0; color:#f3e7c7; }
        .hero-panel p { color:#c6bdad; margin:.4rem 0 1.1rem; }
        .eyebrow { color:var(--gold); font-size:.72rem; font-weight:700; letter-spacing:.14em; }
        .hero-meta { color:#938a78; font-size:.68rem; letter-spacing:.1em; }
        .hero-meta span { color:var(--gold); padding:0 .4rem; }
        .insight-card { background:#1c1b1b; border-left:3px solid var(--gold2); padding:14px 17px; margin-top:12px; border-radius:4px 8px 8px 4px; }
        .insight-card p { color:#d5cdbd; margin:.55rem 0; }
        .time-chip { color:var(--gold); background:#30291a; border:1px solid #4a3e1e; padding:7px 10px; text-align:center; border-radius:6px; font-size:1.05rem; font-weight:700; }
        .auth-shell { text-align:center; padding:2.3rem 0 1.5rem; }
        .auth-mark { color:var(--gold); font-size:2rem; border:1px solid #75602d; border-radius:50%; width:58px; height:58px; line-height:56px; margin:0 auto 1rem; }
        .auth-shell h1 { color:var(--gold); font-size:clamp(2.6rem,6vw,4.2rem); margin:.35rem 0; }
        .auth-subtitle { color:#c5bcab; font-family:Georgia,serif; font-style:italic; font-size:1.1rem; }
        [data-testid="stRadio"] > div { gap:.7rem; }
        [data-testid="stRadio"] label { color:#d9d2c3; }
        [data-baseweb="input"], [data-baseweb="input"] > div, [data-testid="stTextInput"] > div div[data-baseweb="input"] {
          background:#1b1a1a !important; border:1px solid #3a362c !important; border-radius:7px !important;
        }
        [data-testid="stTextInputRootElement"] { background:#1b1a1a !important; border:1px solid #3a362c !important; border-radius:7px !important; }
        [data-testid="stTextInputField"] { background:transparent !important; color:#f5f2eb !important; }
        [data-baseweb="input"] input, [data-testid="stTextInput"] input, [data-testid="stNumberInput"] input, textarea {
          background:transparent !important; color:#f5f2eb !important; border:0 !important;
        }
        [data-baseweb="input"] button { background:#1b1a1a !important; color:#b7ad99 !important; }
        [data-testid="stRadio"] input[type="radio"] { accent-color:#d4af37 !important; }
        label[data-testid="stRadioOption"][data-selected="true"] > div > div:first-child,
        label[data-testid="stRadioOption"][data-selected="true"] > div > div > div { background:#d4af37 !important; border-color:#d4af37 !important; }
        button[aria-label="Show password"], button[aria-label="Hide password"] { background:#1b1a1a !important; color:#b7ad99 !important; }
        [data-testid="stRadio"] [role="radio"] > div:first-child { border-color:#89723b !important; }
        [data-testid="stRadio"] [role="radio"][aria-checked="true"] > div:first-child { background:#d4af37 !important; border-color:#d4af37 !important; }
        hr { border-color:#37342c; }
        @media (max-width:700px) { .block-container { padding:1rem 1rem 2rem; } .hero-panel { padding:22px; } }
        </style>
        """,
        unsafe_allow_html=True,
    )


def main():
    st.set_page_config(page_title="Golden Time", page_icon="⏳", layout="wide")
    render_styles()
    initialize_auth_db()

    if not st.session_state.get("account"):
        render_auth_page()
        return

    account = st.session_state["account"]
    data = read_data()
    title_col, nav_col, account_col = st.columns([1.2, 3.5, 1.3])
    with title_col:
        st.markdown("### <span style='color:#f2ca50'>GOLDEN TIME</span>", unsafe_allow_html=True)
        st.caption("PERSONAL SCHEDULE INTELLIGENCE")
    with nav_col:
        pages = ["Dashboard", "Profiler", "Kebiasaan", "Insights"]
        current_page = st.session_state.get("active_page", "Dashboard")
        page = st.radio(
            "Navigasi",
            pages,
            index=pages.index(current_page) if current_page in pages else 0,
            horizontal=True,
            key="active_page",
            label_visibility="collapsed",
        )
    with account_col:
        st.caption(account["email"])
        if st.button("Keluar", key="logout"):
            del st.session_state["account"]
            st.session_state["active_page"] = "Dashboard"
            st.rerun()

    if not data["profile"].get("onboarding_complete") and page != "Profiler":
        st.info("Lengkapi profiler jadwal agar usulan kebiasaan dapat menyesuaikan jam kerja, waktu istirahat, dan preferensi fokusmu.")
        if st.button("Atur konteks jadwal", type="primary"):
            st.session_state["active_page"] = "Profiler"
            st.rerun()

    if page == "Dashboard":
        render_home(data)
    elif page == "Profiler":
        render_profiler(data)
    elif page == "Kebiasaan":
        render_habits(data)
    else:
        render_insights(data)


if __name__ == "__main__":
    main()
