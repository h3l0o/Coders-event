from ortools.sat.python import cp_model
import pandas as pd

class AdvancedTimetableScheduler:
    def __init__(self, university_config):
        self.days = university_config["working_days"]
        self.periods_per_day = university_config["periods_per_day"]
        self.breaks = university_config.get("breaks", [])
        
        self.total_slots = len(self.days) * self.periods_per_day
        
        self.rooms = []
        self.faculty = {}
        self.courses = []
        
    def add_room(self, room_id, room_type, capacity):
        self.rooms.append({"id": room_id, "type": room_type, "capacity": capacity})
        
    def add_faculty(self, faculty_id, unavailable_slots=None):
        # Convert (Day, Period) string tuples to 1D integer timeline indices
        unavail_indices = []
        if unavailable_slots:
            for day, period in unavailable_slots:
                day_idx = self.days.index(day)
                unavail_indices.append(day_idx * self.periods_per_day + period)
        self.faculty[faculty_id] = unavail_indices
        
    def add_course(self, code, name, prog_sem, is_practical, faculty_id, weekly_periods, consecutive_required=1, required_room_type="Classroom", batch_info=None, student_strength=40):
        self.courses.append({
            "code": code, "name": name, "prog_sem": prog_sem, "is_practical": is_practical,
            "faculty": faculty_id, "weekly_periods": weekly_periods, "consec": consecutive_required,
            "required_room_type": required_room_type, "batch_info": batch_info or [None],
            "strength": student_strength
        })

    def generate_timetable(self, max_time_seconds=15.0):
        model = cp_model.CpModel()
        
        # Resource overlap trackers
        room_intervals = {r["id"]: [] for r in self.rooms}
        faculty_intervals = {f: [] for f in self.faculty}
        group_intervals = {} # Key: prog_sem
        
        session_data = [] # To reconstruct the schedule later
        
        # Optimization objective: minimize late-day scheduling
        objective_terms = []

        for c_idx, course in enumerate(self.courses):
            prog_sem = course["prog_sem"]
            if prog_sem not in group_intervals:
                group_intervals[prog_sem] = []
                
            batches = course["batch_info"]
            consec = course["consec"]
            num_sessions = course["weekly_periods"] // consec
            
            # Determine eligible rooms
            req_capacity = course["strength"] if course["batch_info"] == [None] else course["strength"] // len(batches)
            valid_rooms = [r["id"] for r in self.rooms if r["type"] == course["required_room_type"] and r["capacity"] >= req_capacity]
            
            if not valid_rooms:
                return None, f"Configuration Error: No valid room for {course['code']}"

            for s_num in range(num_sessions):
                for batch in batches:
                    # 1. Build Valid Start Domain (1D Timeline)
                    valid_start_times = []
                    for d in range(len(self.days)):
                        for p in range(self.periods_per_day - consec + 1):
                            # Ensure session doesn't cross into breaks or faculty unavailable times[cite: 1]
                            slots = [d * self.periods_per_day + p + offset for offset in range(consec)]
                            if all((s % self.periods_per_day) not in self.breaks for s in slots):
                                if all(s not in self.faculty[course["faculty"]] for s in slots):
                                    valid_start_times.append(slots[0])
                    
                    if not valid_start_times:
                        return None, f"Infeasible: No valid slots for {course['code']} considering breaks and faculty availability."

                    # 2. Main Session Variables
                    start_var = model.NewIntVarFromDomain(
                        cp_model.Domain.FromValues(valid_start_times), 
                        f"start_{c_idx}_{s_num}_{batch}"
                    )
                    end_var = model.NewIntVar(0, self.total_slots, f"end_{c_idx}_{s_num}_{batch}")
                    session_interval = model.NewIntervalVar(start_var, consec, end_var, f"interval_{c_idx}_{s_num}_{batch}")
                    
                    # 3. Add to Faculty and Group Overlap trackers[cite: 1]
                    faculty_intervals[course["faculty"]].append(session_interval)
                    group_intervals[prog_sem].append(session_interval)
                    
                    # 4. Optional Intervals for Alternative Rooms (Highly Efficient)
                    room_presence_vars = []
                    assigned_room_var = model.NewIntVar(0, len(valid_rooms) - 1, f"room_idx_{c_idx}_{s_num}_{batch}")
                    
                    for r_idx, r_id in enumerate(valid_rooms):
                        is_present = model.NewBoolVar(f"pres_{c_idx}_{s_num}_{batch}_{r_id}")
                        room_presence_vars.append(is_present)
                        
                        # Link integer room choice to boolean presence
                        model.Add(assigned_room_var == r_idx).OnlyEnforceIf(is_present)
                        model.Add(assigned_room_var != r_idx).OnlyEnforceIf(is_present.Not())
                        
                        opt_interval = model.NewOptionalIntervalVar(
                            start_var, consec, end_var, is_present, 
                            f"opt_int_{c_idx}_{s_num}_{batch}_{r_id}"
                        )
                        room_intervals[r_id].append(opt_interval)
                    
                    # Ensure exactly one room is chosen[cite: 2]
                    model.AddExactlyOne(room_presence_vars)
                    
                    # Encourage earlier slots in the day (Optimization preference)
                    objective_terms.append(start_var)
                    
                    session_data.append({
                        "course": course, "batch": batch, "start_var": start_var,
                        "room_var": assigned_room_var, "valid_rooms": valid_rooms
                    })

        # 5. Apply Native NoOverlap Constraints (The core efficiency upgrade)[cite: 1]
        for f_intervals in faculty_intervals.values():
            if f_intervals: model.AddNoOverlap(f_intervals)
            
        for g_intervals in group_intervals.values():
            if g_intervals: model.AddNoOverlap(g_intervals)
            
        for r_intervals in room_intervals.values():
            if r_intervals: model.AddNoOverlap(r_intervals)

        # 6. Objective: Minimize the sum of start times (packs classes earlier in the day)
        model.Minimize(sum(objective_terms))

        # Solve
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = max_time_seconds
        # Optional: Print search progress to console
        # solver.parameters.log_search_progress = True 
        
        status = solver.Solve(model)
        
        if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            results = []
            for data in session_data:
                start_val = solver.Value(data["start_var"])
                room_idx = solver.Value(data["room_var"])
                
                day_idx = start_val // self.periods_per_day
                period_idx = start_val % self.periods_per_day
                course = data["course"]
                consec = course["consec"]
                
                for offset in range(consec):
                    results.append({
                        "Programme/Sem": course["prog_sem"],
                        "Subject Code": course["code"],
                        "Type": "Practical" if course["is_practical"] else "Theory",
                        "Batch": data["batch"] if data["batch"] else "Full Class",
                        "Faculty": course["faculty"],
                        "Room": data["valid_rooms"][room_idx],
                        "Day": self.days[day_idx],
                        "Period": period_idx + offset + 1
                    })
            
            quality = "Optimal (Best possible schedule)" if status == cp_model.OPTIMAL else "Feasible (Time limit reached)"
            return pd.DataFrame(results).sort_values(by=["Programme/Sem", "Day", "Period"]), f"Success: {quality}"
        else:
            return None, "Error: Impossible timetable requirements. Constraints cannot be met."

# ==========================================
# Example Execution
# ==========================================
if __name__ == "__main__":
    config = {
        "working_days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
        "periods_per_day": 6,
        "breaks": [2] # 3rd period is break (index 2)
    }
    
    scheduler = AdvancedTimetableScheduler(config)
    scheduler.add_room("Lab-101", "Computer Lab", 25)
    scheduler.add_room("Class-201", "Classroom", 60)
    
    scheduler.add_faculty("Dr. Sharma", unavailable_slots=[("Mon", 0)]) 
    scheduler.add_faculty("Dr. Verma")
    
    scheduler.add_course("DFIS101", "Digital Forensics", "M.Sc. DFIS - Sem I", False, "Dr. Sharma", 3, 1, "Classroom", student_strength=40)
    scheduler.add_course("DFIS103", "Cyber Security Lab", "M.Sc. DFIS - Sem I", True, "Dr. Verma", 2, 2, "Computer Lab", ["Batch_A", "Batch_B"], student_strength=40)
    
    df, status = scheduler.generate_timetable()
    print(status)
    if df is not None:
        print(df.to_string(index=False))
