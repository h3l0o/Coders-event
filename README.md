⚡ Automated Academic Timetable Generator

Built for the NFSU Open Internet Coder's Event
Problem Statement #1: Automated Academic Timetable Generation System

A highly efficient, web-based timetable scheduling system that uses Mathematical Constraint Programming to generate zero-clash academic schedules. It allows coordinators to dynamically input university resources and automatically solves for a timetable that respects all mandatory constraints.

✨ Key Features

Mathematical Optimization Engine: Powered by Google OR-Tools (CP-SAT Solver), ensuring deterministic, 100% clash-free schedules (no genetic algorithm guesswork).

Interactive Web UI: Built with Streamlit for a fast, reactive coordinator dashboard.

Dynamic Resource Management: Add, edit, or delete university timings, classrooms, faculty availability, and subject requirements on the fly.

Smart Constraint Handling:

Automatically avoids scheduled university breaks.

Keeps multi-period practical labs consecutive without splitting.

Prevents double-booking of faculty, rooms, and student batches.

Enforces capacity checks (e.g., 40 students cannot be scheduled in a 25-seat lab).

Impossible Schedule Diagnostics: If constraints are too tight, the pre-flight diagnostic engine clearly reports exactly why it failed (e.g., "Faculty Overload") rather than crashing.

Multi-View Matrices: View generated schedules Programme-wise, Faculty-wise, or Room-wise.

State Management & Export: Export timetables as CSV, or download the entire environment state as a JSON file to easily reproduce configurations.

🧠 How It Works (The Architecture)

This system frames the scheduling challenge as a Constraint Satisfaction Problem (CSP). Instead of using standard brute-force if-else loops, it models the university week as a 1D discrete timeline.

Interval Variables: Theory and Practical sessions are modeled as unbroken intervals of time.

Domain Reduction: Invalid slots (like faculty absence or university breaks) are pruned from the domain before the solver even starts searching.

No-Overlap Constraints: Advanced edge-finding algorithms ensure that full classes do not overlap with their own practical batches (Rule 4), and multiple sessions of the same subject are mathematically forced onto different days to balance student workloads.

🚀 Installation & Setup

1. Clone the repository:

git clone https://github.com/h3l0o/Coders-event.git
cd Coders-event


2. Install required dependencies:
Make sure you have Python installed, then run:

pip install streamlit ortools pandas


3. Run the application:

streamlit run app.py


The web interface will automatically open in your default browser at http://localhost:8501.

📖 Usage Walkthrough (For Coordinators)

The application is divided into four main tabs:

📝 Dynamic Coordinator Inputs:
Define your university working days, set break periods, register rooms (with capacities), and list faculty (with their unavailable slots). Add course requirements, distinguishing between single-period theory lectures and multi-period batch practicals.

🚀 Solver & Conflicts:
Click Generate Automatic Timetable. The CP-SAT engine will attempt to solve the matrix. If the workload is physically impossible, diagnostic warnings will appear here.

📊 Views & Matrices:
Review the successfully generated timetable. Use the dropdowns to switch between tabular data and visual weekly grids.

💾 Export & JSON:
Download the final timetable as a .csv file, or save/load the raw .json state to quickly test different university configurations.

Created for National Forensic Sciences University (NFSU) Hackathon / Coder's Event.
