# AI Physics Alexis
### Summary 
The purpose of this assignment was to upload to GitHub a Claude integrated jupyter notebook environment and create a query function that takes questions as string inputs,feeds it to Claude and outputs a response within the jupyternotebook. The Claude model integrated into the Jupyter notebook for all queries was the claude-haiku-4-5-20251001".
Three physics problems from quantum mechanics and astrophysics textbooks[1][2]were placed as inputs in the query function[3][4], and the outputted numerical result has been featured in the document and compared with the true calculated results from their respective textbooks. The discrepancy percentage was calculated using this formula below. 
percent_error = (abs(C - T) / T)* 100
C = Claude Value B = Textbook Value
The contents of this document are the results of rerunning the jupyternotebook script four separate times to ensure the accuracy/consistency of the statements made in this document. While Claude was able to do very basic simple calculations for the first two problems, the general relativity problem resulted in large discrepancies. LLM’s are good for basic numerical calculations or brief overview/summaries of papers inputted, however when it comes to solving questions that require large values raised to larger order powers in a sequence of multistep physics problems the Key/Query algorithm will not produce the correct or consistent values because somewhere along those large sequences of matrix multiplications a calculation may be dropped or skipped or considered not as relevant and thus the final result will be incorrect. 

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

  ## SOURCES

[1] Zettili, N. (2009). Quantum mechanics: Concepts and Applications (3rd ed., p. 22). Wiley. 

[2] Carroll, B. W., & Ostlie, D. A. (2007). Introduction to modern astrophysics (2nd ed., p. 36). Pearson. 

[3] OpenAI. (2026). Developer quickstart. Retrieved from https://platform.openai.com/docs/quickstart

[4] Anthropic. (2026). Claude (Version Haiku 4.5) [Large language model]. https://claude.ai

