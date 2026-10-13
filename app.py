import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import json

# Set Page Config
st.set_page_config(page_title="Academic Timetable Generator", page_icon="📅", layout="wide")

# ==========================================
# 1. CORE ENGINE (OR-Tools CP-SAT)
# ==========================================
class DynamicSchedulerEngine:
    def __init__(self, data):
        self.config = data["config"]
        self.days = self.config["working_days"]
        self.periods_per_day = self.config["periods_per_day"]
        self.breaks = self.config.get("breaks", [])
        self.rooms = data["rooms"]
        self.faculty = {f["id"]: f.get("unavailable_slots", []) for f in data["faculty"]}
        self.courses = data["courses"]
        self.locked_sessions = data.get("locked_sessions", [])

    def pre_flight_checks(self):
        """Diagnoses impossible timetable constraints prior to or upon failure."""
        errors = []
        total_week_slots = len(self.days) * (self.periods_per_day - len(self.breaks))

        # Check Room Availability & Capacities
        for c in self.courses:
            needed_cap = c["student_strength"] if not c.get("batch_info") else c["student_strength"] // len(c["batch_info"])
            matching_rooms = [
                r for r in self.rooms 
                if r["type"] == c["required_room_type"] and r["capacity"] >= needed_cap
            ]
            if not matching_rooms:
                errors.append(
                    f"Room Mismatch: Subject '{c['code']}' requires room type '{c['required_room_type']}' "
                    f"with capacity >= {needed_cap}, but no suitable room exists."
                )

        # Check Faculty Workload vs Availability
        fac_demand = {}
        for c in self.courses:
            fac = c["faculty"]
            fac_demand[fac] = fac_demand.get(fac, 0) + c["weekly_periods"]

        for fac_id, total_periods in fac_demand.items():
            unavail_count = len(self.faculty.get(fac_id, []))
            available_periods = (len(self.days) * self.periods_per_day) - unavail_count
            if total_periods > available_periods:
                errors.append(
                    f"Faculty Overload: Faculty '{fac_id}' is assigned {total_periods} periods, "
                    f"but is only available for {available_periods} periods."
                )

        return errors

    def solve(self, max_time_seconds=10.0):
        pre_errors = self.pre_flight_checks()
        if pre_errors:
            return None, pre_errors

        model = cp_model.CpModel()
        room_intervals = {r["id"]: [] for r in self.rooms}
        faculty_intervals = {f_id: [] for f_id in self.faculty}
        programme_intervals = {}
        batch_intervals = {}

        session_records = []
        objective_terms = []

        for c_idx, course in enumerate(self.courses):
            prog = course["programme"]
            if prog not in programme_intervals:
                programme_intervals[prog] = []

            batches = course.get("batch_info") or [None]
            consec = course["consecutive_periods"]
            num_sessions = course["weekly_periods"] // consec
            req_cap = course["student_strength"] if not course.get("batch_info") else course["student_strength"] // len(batches)

            eligible_rooms = [
                r["id"] for r in self.rooms 
                if r["type"] == course["required_room_type"] and r["capacity"] >= req_cap
            ]

            for s_num in range(num_sessions):
                for batch in batches:
                    batch_key = f"{prog}_{batch}" if batch else None
                    if batch_key and batch_key not in batch_intervals:
                        batch_intervals[batch_key] = []

                    # Find valid start periods
                    valid_starts = []
                    for d_idx in range(len(self.days)):
                        for p_idx in range(self.periods_per_day - consec + 1):
                            slot_range = [p_idx + offset for offset in range(consec)]
                            # Break constraint
                            if any(p in self.breaks for p in slot_range):
                                continue
                            # Faculty unavailability constraint
                            fac_unavail = self.faculty.get(course["faculty"], [])
                            if any([self.days[d_idx], p] in fac_unavail for p in slot_range):
                                continue
                            valid_starts.append(d_idx * self.periods_per_day + p_idx)

                    if not valid_starts:
                        return None, [f"No valid consecutive slots available for {course['code']} (Check breaks & faculty unavailability)."]

                    # Decision Variables
                    start_var = model.NewIntVarFromDomain(
                        cp_model.Domain.FromValues(valid_starts), 
                        f"start_{c_idx}_{s_num}_{batch}"
                    )
                    end_var = model.NewIntVar(0, len(self.days) * self.periods_per_day, f"end_{c_idx}_{s_num}_{batch}")
                    sess_interval = model.NewIntervalVar(start_var, consec, end_var, f"interval_{c_idx}_{s_num}_{batch}")

                    # Constraints
                    faculty_intervals[course["faculty"]].append(sess_interval)
                    if not batch:
                        programme_intervals[prog].append(sess_interval)
                    else:
                        batch_intervals[batch_key].append(sess_interval)

                    # Room Choice
                    room_idx_var = model.NewIntVar(0, len(eligible_rooms) - 1, f"room_{c_idx}_{s_num}_{batch}")
                    room_presence = []
                    for r_i, r_id in enumerate(eligible_rooms):
                        pres = model.NewBoolVar(f"pres_{c_idx}_{s_num}_{batch}_{r_id}")
                        room_presence.append(pres)
                        model.Add(room_idx_var == r_i).OnlyEnforceIf(pres)
                        model.Add(room_idx_var != r_i).OnlyEnforceIf(pres.Not())

                        opt_int = model.NewOptionalIntervalVar(start_var, consec, end_var, pres, f"opt_{c_idx}_{s_num}_{batch}_{r_id}")
                        room_intervals[r_id].append(opt_int)

                    model.AddExactlyOne(room_presence)

                    # Soft preference: prefer earlier slots in the day
                    objective_terms.append(start_var)

                    session_records.append({
                        "course": course,
                        "batch": batch,
                        "start_var": start_var,
                        "room_idx_var": room_idx_var,
                        "eligible_rooms": eligible_rooms
                    })

        # Apply No-Overlap constraints
        for f_ints in faculty_intervals.values():
            if f_ints: model.AddNoOverlap(f_ints)
        for r_ints in room_intervals.values():
            if r_ints: model.AddNoOverlap(r_ints)
            
        # FIX: Rule 4 - A whole-class session cannot overlap with its own practical batches
        for prog in programme_intervals:
            full_class_ints = programme_intervals.get(prog, [])
            # Find all batches that belong to this specific programme
            prog_batches = [b_key for b_key in batch_intervals.keys() if b_key.startswith(prog + "_")]
            
            if not prog_batches:
                if full_class_ints: model.AddNoOverlap(full_class_ints)
            else:
                for b_key in prog_batches:
                    # Combine the full class and the specific batch into one strict No-Overlap list
                    combined_ints = full_class_ints + batch_intervals[b_key]
                    if combined_ints: model.AddNoOverlap(combined_ints)

        # (We removed the Minimize function here so it stops cramming everything onto Monday)
        # Solve
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = max_time_seconds
        status = solver.Solve(model)

        if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            timetable = []
            for item in session_records:
                s_val = solver.Value(item["start_var"])
                r_val = solver.Value(item["room_idx_var"])
                day_idx = s_val // self.periods_per_day
                p_idx = s_val % self.periods_per_day
                c = item["course"]

                for offset in range(c["consecutive_periods"]):
                    timetable.append({
                        "Day": self.days[day_idx],
                        "Period": p_idx + offset + 1,
                        "Programme": c["programme"],
                        "Subject Code": c["code"],
                        "Subject Name": c["name"],
                        "Type": "Practical" if c["is_practical"] else "Theory",
                        "Batch": item["batch"] if item["batch"] else "All Batches",
                        "Faculty": c["faculty"],
                        "Room": item["eligible_rooms"][r_val]
                    })
            return pd.DataFrame(timetable), []
        else:
            return None, ["The timetable requirements cannot be satisfied under the current resource and time limits."]


# ==========================================
# 2. STREAMLIT INTERACTIVE UI
# ==========================================
if "data" not in st.session_state:
    # Default State with NFSU Problem Statement #1 example loaded
    st.session_state.data = {
        "config": {
            "working_days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
            "periods_per_day": 6,
            "breaks": [2] # 3rd period is break (0-indexed: 2)
        },
        "rooms": [
            {"id": "Lab-101", "type": "Cyber Lab", "capacity": 25},
            {"id": "Class-201", "type": "Classroom", "capacity": 50}
        ],
        "faculty": [
            {"id": "Dr. Sharma", "unavailable_slots": [["Mon", 0]]},
            {"id": "Dr. Verma", "unavailable_slots": []}
        ],
        "courses": [
            {
                "code": "DFIS101", "name": "Digital Forensics", "programme": "M.Sc. DFIS - Sem I",
                "is_practical": False, "faculty": "Dr. Sharma", "weekly_periods": 3,
                "consecutive_periods": 1, "required_room_type": "Classroom",
                "student_strength": 40, "batch_info": None
            },
            {
                "code": "DFIS103", "name": "Cyber Security Practical", "programme": "M.Sc. DFIS - Sem I",
                "is_practical": True, "faculty": "Dr. Verma", "weekly_periods": 2,
                "consecutive_periods": 2, "required_room_type": "Cyber Lab",
                "student_strength": 40, "batch_info": ["Batch_A", "Batch_B"]
            }
        ]
    }

st.title("⚡ Automated Academic Timetable Generation System")
st.caption("National Forensic Sciences University (NFSU) — Dynamic Scheduler")

tabs = st.tabs(["📝 Dynamic Coordinator Inputs", "🚀 Solver & Conflicts", "📊 Views & Matrices", "💾 Export & JSON"])

# ----------------- TAB 1: INPUTS -----------------
with tabs[0]:
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("University Timings")
        days_input = st.text_input("Working Days (comma separated)", value=",".join(st.session_state.data["config"]["working_days"]))
        periods_input = st.number_input("Periods per Day", min_value=1, max_value=12, value=st.session_state.data["config"]["periods_per_day"])
        breaks_input = st.text_input("Break Period Indices (0-indexed, comma separated)", value=",".join(map(str, st.session_state.data["config"]["breaks"])))

        if st.button("Save University Timings"):
            st.session_state.data["config"]["working_days"] = [d.strip() for d in days_input.split(",") if d.strip()]
            st.session_state.data["config"]["periods_per_day"] = int(periods_input)
            st.session_state.data["config"]["breaks"] = [int(b.strip()) for b in breaks_input.split(",") if b.strip().isdigit()]
            st.success("Timings updated successfully!")

    with col2:
        st.subheader("Add Classroom / Lab")
        r_id = st.text_input("Room Identifier", placeholder="e.g. Lab-302")
        r_type = st.selectbox("Room Type", ["Classroom", "Cyber Lab", "Forensics Lab", "Auditorium"])
        r_cap = st.number_input("Seating Capacity", min_value=10, max_value=500, value=40)
        if st.button("Add Room"):
            if r_id and not any(r["id"] == r_id for r in st.session_state.data["rooms"]):
                st.session_state.data["rooms"].append({"id": r_id, "type": r_type, "capacity": r_cap})
                st.success(f"Added {r_id}")
            else:
                st.error("Invalid or duplicate Room ID.")

    st.markdown("---")
    col3, col4 = st.columns(2)
    with col3:
        st.subheader("Add Faculty & Availability")
        f_id = st.text_input("Faculty Name / ID", placeholder="e.g. Dr. Patel")
        unavail_str = st.text_input("Unavailable Slots (Format: Day:Period, comma separated)", placeholder="e.g. Mon:0, Tue:3")
        if st.button("Add Faculty"):
            if f_id and not any(f["id"] == f_id for f in st.session_state.data["faculty"]):
                parsed_unavail = []
                if unavail_str:
                    for item in unavail_str.split(","):
                        parts = item.strip().split(":")
                        if len(parts) == 2 and parts[1].isdigit():
                            parsed_unavail.append([parts[0].strip(), int(parts[1].strip())])
                st.session_state.data["faculty"].append({"id": f_id, "unavailable_slots": parsed_unavail})
                st.success(f"Added {f_id}")
            else:
                st.error("Duplicate or missing faculty name.")

    with col4:
        st.subheader("Add Subject / Course Requirement")
        c_code = st.text_input("Subject Code", placeholder="e.g. DFIS201")
        c_name = st.text_input("Subject Name", placeholder="e.g. Malware Analysis")
        c_prog = st.text_input("Programme & Sem", value="M.Sc. DFIS - Sem I")
        is_prac = st.checkbox("Is Practical Session?")
        c_fac = st.selectbox("Assigned Faculty", [f["id"] for f in st.session_state.data["faculty"]])
        c_periods = st.number_input("Weekly Periods", min_value=1, max_value=10, value=3)
        c_consec = st.number_input("Consecutive Periods per Session", min_value=1, max_value=4, value=2 if is_prac else 1)
        c_room_type = st.selectbox("Required Room Type", ["Classroom", "Cyber Lab", "Forensics Lab", "Auditorium"])
        c_batches = st.text_input("Batch Names (Optional, comma separated)", placeholder="e.g. Batch_A, Batch_B" if is_prac else "")
        c_strength = st.number_input("Class Student Strength", min_value=5, max_value=200, value=40)

        if st.button("Add Subject Requirement"):
            batches_parsed = [b.strip() for b in c_batches.split(",") if b.strip()] if c_batches else None
            st.session_state.data["courses"].append({
                "code": c_code, "name": c_name, "programme": c_prog,
                "is_practical": is_prac, "faculty": c_fac, "weekly_periods": c_periods,
                "consecutive_periods": c_consec, "required_room_type": c_room_type,
                "student_strength": c_strength, "batch_info": batches_parsed
            })
            st.success(f"Added Subject {c_code}")

    st.markdown("---")
    st.write("### Current Registered Resources")
    st.write("**Rooms:**", pd.DataFrame(st.session_state.data["rooms"]))
    st.write("**Courses:**", pd.DataFrame(st.session_state.data["courses"]))
    st.markdown("---")
    st.subheader("🗑️ Manage & Delete Resources")
    
    # Create 3 columns for our delete buttons
    del_col1, del_col2, del_col3 = st.columns(3)

    # 1. Delete Room
    with del_col1:
        if st.session_state.data["rooms"]:
            room_to_del = st.selectbox("Select Room to Delete", [r["id"] for r in st.session_state.data["rooms"]])
            if st.button("Delete Room"):
                # Rebuild the list WITHOUT the selected room
                st.session_state.data["rooms"] = [r for r in st.session_state.data["rooms"] if r["id"] != room_to_del]
                st.rerun() # Instantly refreshes the web page

    # 2. Delete Faculty
    with del_col2:
        if st.session_state.data["faculty"]:
            fac_to_del = st.selectbox("Select Faculty to Delete", [f["id"] for f in st.session_state.data["faculty"]])
            if st.button("Delete Faculty"):
                st.session_state.data["faculty"] = [f for f in st.session_state.data["faculty"] if f["id"] != fac_to_del]
                st.rerun()

    # 3. Delete Course
    with del_col3:
        if st.session_state.data["courses"]:
            course_to_del = st.selectbox("Select Course to Delete", [c["code"] for c in st.session_state.data["courses"]])
            if st.button("Delete Course"):
                st.session_state.data["courses"] = [c for c in st.session_state.data["courses"] if c["code"] != course_to_del]
                st.rerun() 

# ----------------- TAB 2: SOLVER & CONFLICTS -----------------
with tabs[1]:
    st.header("Timetable Generation & Diagnostic Engine")
    st.info("The solver enforces zero collisions for rooms, faculty, and student groups, while honoring scheduled university breaks.")

    timeout = st.slider("Max Solver Search Time (seconds)", min_value=2, max_value=30, value=5)

    if st.button("⚡ Generate Automatic Timetable", type="primary"):
        with st.spinner("Executing CP-SAT Constraint Optimization..."):
            scheduler = DynamicSchedulerEngine(st.session_state.data)
            result_df, errors = scheduler.solve(max_time_seconds=timeout)

            if result_df is not None:
                st.session_state.result_df = result_df
                st.success("✅ Timetable Generated Successfully with Zero Violations!")
            else:
                st.session_state.result_df = None
                st.error("❌ Schedule Impossible to Accommodate!")
                st.subheader("Scheduling Conflict Diagnostics:")
                for err in errors:
                    st.warning(f"⚠️ {err}")

# ----------------- TAB 3: VIEWS & MATRICES -----------------
with tabs[2]:
    st.header("Timetable Reviews & Multi-View Matrices")
    if "result_df" in st.session_state and st.session_state.result_df is not None:
        df = st.session_state.result_df

        view_mode = st.radio("Select View Mode", ["Programme-wise", "Faculty-wise", "Classroom-wise"], horizontal=True)

        if view_mode == "Programme-wise":
            progs = df["Programme"].unique()
            selected_prog = st.selectbox("Select Programme", progs)
            filtered = df[df["Programme"] == selected_prog]
        elif view_mode == "Faculty-wise":
            facs = df["Faculty"].unique()
            selected_fac = st.selectbox("Select Faculty", facs)
            filtered = df[df["Faculty"] == selected_fac]
        else:
            rms = df["Room"].unique()
            selected_rm = st.selectbox("Select Room", rms)
            filtered = df[df["Room"] == selected_rm]

        st.subheader("Detailed Session List")
        st.dataframe(filtered, use_container_width=True)

        st.subheader("Visual Weekly Grid")
        # Build pivot table representation
        filtered["Slot_Display"] = filtered["Subject Code"] + " (" + filtered["Room"] + ")"
        pivot_grid = filtered.pivot_table(
            index="Period", 
            columns="Day", 
            values="Slot_Display", 
            aggfunc=lambda x: " / ".join(x)
        ).reindex(columns=st.session_state.data["config"]["working_days"]).fillna("—")
        st.table(pivot_grid)
    else:
        st.info("No timetable generated yet. Run the solver in Tab 2.")

# ----------------- TAB 4: EXPORT & RAW JSON -----------------
with tabs[3]:
    st.header("Save, Publish, and Export")
    if "result_df" in st.session_state and st.session_state.result_df is not None:
        csv_data = st.session_state.result_df.to_csv(index=False).encode('utf-8')
        st.download_button("📥 Export Generated Timetable as CSV", csv_data, "timetable.csv", "text/csv")
    
    st.subheader("Import / Export Full System State (JSON)")
    st.download_button(
        "Save State to JSON", 
        json.dumps(st.session_state.data, indent=2), 
        "timetable_config.json", 
        "application/json"
    )

    uploaded_file = st.file_uploader("Load State from JSON File", type=["json"])
    if uploaded_file is not None:
        try:
            loaded_data = json.load(uploaded_file)
            st.session_state.data = loaded_data
            st.success("Configuration loaded! Switch to Tab 2 to solve.")
        except Exception as e:
            st.error(f"Error loading JSON: {e}")
