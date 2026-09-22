# AI Physics Alexis
## Setup Instructions
Follow these steps to set up and run the project:
### Step 1: Clone the Repository
Clone the git repository to your local machine:
```bash
git clone https://github.com/bhalex86/ai-physics-alexis.git
```
### Step 2: Navigate to the Project Directory
Access the cloned repository:
```bash
cd ai-physics-alexis
```
### Step 3: Create a Virtual Environment
Create a Python 3 virtual environment:
```bash
python3 -m venv .venv
```
### Step 4: Activate the Virtual Environment
Activate the virtual environment:
```bash
source .venv/bin/activate
```
### Step 5: Install Dependencies and add the API KEY provided from class
Install all required packages:
```bash
pip install -r requirements.txt
echo "ANTHROPIC_API_KEY=your_api_key_here" > .env 
```
### Step 6: Launch Jupyter Notebook
Start the Jupyter notebook:
```bash
jupyter notebook
```

---

## Quick Start (All Commands)

Run all commands in sequence:

```bash
git clone https://github.com/bhalex86/ai-physics-alexis.git
cd ai-physics-alexis
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
echo "ANTHROPIC_API_KEY=your_api_key_here" > .env
jupyter notebook
```

---

## Notes

- Make sure you have **Python 3** installed on your system
- The virtual environment (`.venv`) isolates project dependencies
- Your Jupyter notebook will open in your default browser (usually at `http://localhost:8888`)
- To deactivate the virtual environment when finished, run: `deactivate`
- This README.MD file was generated using Claude AI Haiku 4.5 for formatting purposes and organization given the inputted Quick Start Commands
