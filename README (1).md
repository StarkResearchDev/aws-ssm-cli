# 🕵️ The Machine — AWS CLI Tool

A **Person of Interest**-inspired AWS CLI interface that lets you:
- Select one or more AWS EC2 instances from a profile/region
- Run commands remotely via AWS SSM
- Update project repositories on instances (git pull, checkout)
- Append lines to files on remote instances
- Search for files in remote projects
- Execute in **parallel batch mode**
- All with **The Machine**-style Matrix rain intro & typing effect

---

## ✨ Features

- **AWS Profile & Region Selection**  
  Choose from your configured AWS profiles and regions dynamically.

- **Instance Multi-Select**  
  Pick one or many EC2 instances by name or ID.

- **Command Execution**  
  Run arbitrary shell commands over SSM or use predefined scripts.

- **Git Repo Management**  
  - Pull latest changes
  - Checkout to a specific branch

- **File Append Mode**  
  Add a new line **after** a `run()` function in a file remotely.

- **Search Mode**  
  Find files inside a remote repository matching a pattern.

- **Parallel Execution**  
  Run commands on multiple instances simultaneously.

- **The Machine Intro**  
  Green Matrix rain, typing effect, and Person of Interest terminal theme.

---

## 📦 Requirements

### Python packages
```
boto3
colorama
pyfiglet
rich
prompt_toolkit
keyboard
# If on Windows only:
# windows-curses
```

Install:
```bash
pip install -r requirements.txt
```

### AWS Requirements
- `awscli` installed & configured with profiles
- SSM Agent installed & running on instances
- Proper IAM permissions for SSM commands

---

## 🚀 Usage

### Run with intro animation:
```bash
python the_machine_cli.py
```

### Skip intro for quick commands:
```bash
python the_machine_cli.py --skip-intro
```

---

## 📋 Menu Options

1. **Select AWS Profile & Region**  
   Interactive picker to choose credentials and location.

2. **Select Instances**  
   Multi-select from available EC2 instances.

3. **Run Command(s)**  
   Enter commands to execute over SSM.

4. **Git Operations**  
   - Pull latest updates
   - Checkout branch

5. **File Append Mode**  
   Append a line after `run()` in a given file path.

6. **Search Mode**  
   Search for files in a remote project folder.

---

## ⚡ Parallel Mode
For faster operations on multiple instances:
```bash
python the_machine_cli.py --batch --parallel
```

---

## 🎨 Person of Interest Mode
The CLI starts with:
- Matrix rain background
- Typing effect for messages
- ASCII art "The Machine" logo

---

## 🛡 Permissions
Ensure your AWS IAM role/user has:
- `ssm:SendCommand`
- `ssm:DescribeInstanceInformation`
- `ec2:DescribeInstances`
- Any other permissions needed for your commands

---

## 🖥 Demo Screenshot
```
[Initializing...]
█▓▒░  THE MACHINE  ░▒▓█
Searching... Found AWS Profiles.
Please select your target(s)...
```
