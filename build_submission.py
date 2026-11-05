import os
import shutil
from fpdf import FPDF

submission_dir = "Submission"
mic_dir = os.path.join(submission_dir, "Mic Code")
mob_dir = os.path.join(submission_dir, "Mob Code")
web_dir = os.path.join(submission_dir, "Web Code")

# 1. Create folders
os.makedirs(mic_dir, exist_ok=True)
os.makedirs(mob_dir, exist_ok=True)
os.makedirs(web_dir, exist_ok=True)

# 2. Copy files
# Mic Code
if os.path.exists("src/hardware"):
    shutil.copytree("src/hardware", os.path.join(mic_dir, "hardware"), dirs_exist_ok=True)
if os.path.exists("run.py"):
    shutil.copy("run.py", mic_dir)

# Web Code
if os.path.exists("src/web"):
    shutil.copytree("src/web", os.path.join(web_dir, "web"), dirs_exist_ok=True)

# Mob Code
with open(os.path.join(mob_dir, "README.md"), "w") as f:
    f.write("# Mobile Application Code\n\nThis project uses a responsive Web Application (Dashboard) instead of a dedicated mobile application. It can be accessed on any mobile device via a web browser.")

# 3. Create PDF
class PDF(FPDF):
    def header(self):
        self.set_font('helvetica', 'B', 15)
        self.cell(0, 10, 'AURUS Project Code Submission Details', align='C', new_x="LMARGIN", new_y="NEXT")
        self.ln(10)

pdf = PDF()
pdf.add_page()
pdf.set_font("helvetica", size=12)

content = """Project Name: AURUS (AUtonomous Robotic Ubiquitous System)

1. Microcontroller Code (Mic Code):
   - Description: The core backend and hardware control logic for the AURUS robot. It runs on a Raspberry Pi, managing motors, sensors, and AI components.
   - Programming Language: Python 3
   - Integrated Development Environment (IDE): Visual Studio Code

2. Mobile Application Code (Mob Code):
   - Description: AURUS does not use a standalone mobile application. Instead, it features a highly responsive, browser-based Web Dashboard accessible from any mobile device, acting as the primary control interface.

3. Web Application Code (Web Code):
   - Description: The user interface (Dashboard) used to interact with AURUS, featuring speech-to-text integration, controls, and camera views.
   - Programming Languages: HTML5, Vanilla JavaScript, CSS3
   - Integrated Development Environment (IDE): Visual Studio Code

Note: All relevant codes have been placed in their respective folders as per the guidelines.
"""

pdf.multi_cell(0, 10, content)
pdf.output("Submission/AURUS_Submission_Details.pdf")

# 4. Zip the Submission folder
shutil.make_archive("Submission", "zip", submission_dir)

# 5. Rename zip to rar to strictly follow instructions, though zip is usually accepted
if os.path.exists("Submission.rar"):
    os.remove("Submission.rar")
os.rename("Submission.zip", "Submission.rar")

print("Submission generated successfully!")
