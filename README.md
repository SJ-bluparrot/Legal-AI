# SaulLM-AI — AI Legal Complaint Generation System

SaulLM-AI is an AI-powered legal backend that converts natural language case descriptions into structured legal complaints.

The system combines **Saul-7B-Instruct** for legal reasoning and information extraction with **Claude (Anthropic)** for high-quality legal document drafting.

Attorneys can describe a case in plain English and the system automatically:

1. Explains the legal issue
2. Classifies the case type
3. Extracts required legal information
4. Collects missing details via a smart intake system
5. Validates the case information
6. Generates a formal legal complaint
7. Exports the complaint as DOCX or PDF

---

# Features

### AI Legal Assistant

Users can ask legal questions and receive structured legal explanations powered by SaulLM.

### Automatic Case Classification

The system classifies cases into supported legal categories:

* Personal Injury
* Contract Dispute
* Property Damage
* Family Law
* Criminal Defense
* Employment Dispute
* Eminent Domain

### Smart Intake System

After classification, the system collects case information using a dynamic intake loop that:

* Extracts known facts from user input
* Auto-fills detected fields
* Requests only missing information
* Tracks case completion progress

### Validation Engine

Before generating a complaint, the system validates:

* Required fields
* Logical case consistency
* Statute-of-limitations warnings
* Draft readiness score

### Complaint Generation

A structured complaint is generated including:

* Caption
* Parties
* Jurisdiction and Venue
* Factual Allegations
* Causes of Action
* Prayer for Relief
* Jury Demand
* Signature Block

### Document Export

Generated complaints can be exported as:

* DOCX
* PDF

---

# Architecture

User Question
↓
SaulLM Legal Assistant
↓
Case Classification
↓
Legal Element Extraction
↓
Smart Intake Loop
↓
Field Validation
↓
Claude Complaint Drafting
↓
DOCX / PDF Export

---

# Tech Stack

Backend

* Python
* FastAPI

AI Models

* Saul-7B-Instruct
* Claude Sonnet

ML Infrastructure

* PyTorch
* HuggingFace Transformers
* BitsAndBytes (8-bit quantization)

Storage

* SQLite

Document Generation

* python-docx
* PDF generator

---

# Project Structure

```
SaulLM-AI/

app.py
classifier.py
element_extractor.py
entity_extractor.py

intake_router.py
validator.py

complaint_drafter.py
complaint_router.py

docx_generator.py
pdf_generator.py
docx_router.py

utils.py

requirements.txt
saul_env.yml
```

---

# Installation

Clone repository

```
git clone https://github.com/Bluparrot/SaulLM-AI.git
cd SaulLM-AI
```

Create environment

```
conda env create -f saul_env.yml
conda activate saul_env
```

Install dependencies

```
pip install -r requirements.txt
```

---

# Environment Variables

Create a `.env` file.

```
ANTHROPIC_API_KEY=your_anthropic_api_key
API_KEY=your_backend_api_key
```

---

# Running the Server

```
python app.py
```

Server runs at:

```
http://localhost:8000
```

Swagger API documentation:

```
http://localhost:8000/docs
```

---

# API Endpoints

Question Answering

```
POST /questions
```

Intake System

```
POST /intake/start
POST /intake/{case_id}/provide
GET /intake/{case_id}
```

Validation

```
GET /validate/{case_id}
```

Complaint Drafting

```
POST /draft/{case_id}
GET /draft/{case_id}
```

Document Export

```
POST /document/{case_id}
```

---

# Development Status

Completed

* AI legal assistant
* case classification
* legal element extraction
* smart intake system
* validation engine
* complaint drafting
* DOCX and PDF export

Future Work

* Frontend interface
* user authentication
* multi-jurisdiction support
* case management dashboard

---

# Disclaimer

This project is intended for research and development purposes only.

Generated complaints are drafts and must always be reviewed by a licensed attorney before legal filing.

---

# Author

Aditya Soni
