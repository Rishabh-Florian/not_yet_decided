# Qontext Track — EnterpriseBench Dataset Analysis

## Overview

The dataset simulates the internal data landscape of **Inazuma.co**, a fictional Indian D2C enterprise. It was originally published as part of [EnterpriseBench](https://huggingface.co/) (ACL EMNLP 2025) — a benchmark for evaluating LLM agents in enterprise environments. The data spans **10 domains** across structured JSON, unstructured PDFs, CSVs, and code repositories.

**Company:** Inazuma.co  
**Headquarters:** India (prices in ₹, emails timestamped IST)  
**Employees:** 1,260  
**Date span:** 2012–2022 (synthetic)  
**Total data size:** ~117 MB  

---

## Dataset Directory Structure

```
EnterpriseBench/
├── Business_and_Management/
│   ├── clients.json                         (400 records, 318 KB)
│   └── vendors.json                         (400 records, ~50 KB)
├── Collaboration_tools/
│   └── conversations.json                   (2,897 records, 6.0 MB)
├── Customer_Relation_Management/
│   ├── customers.json                       (90 records, ~15 KB)
│   ├── products.json                        (1,351 records, 1.5 MB)
│   ├── sales.json                           (13,510 records, 3.7 MB)
│   ├── Customer Support/
│   │   └── customer_support_chats.json      (1,000 records, 4.0 MB)
│   ├── Customer_orders/
│   │   ├── invoice_<id>.pdf                 (90 files)
│   │   ├── purchase_order_<id>.pdf          (90 files)
│   │   └── shipping_order_<id>.pdf          (90 files)
│   └── Product Sentiment/
│       └── product_sentiment.json           (13,510 records, 21.7 MB)
├── Enterprise Social Platform/
│   └── posts.json                           (971 records, 1.7 MB)
├── Enterprise_mail_system/
│   └── emails.json                          (11,928 records, 17.3 MB)
├── Human_Resource_Management/
│   ├── Employees/
│   │   └── employees.json                   (1,260 records, 2.3 MB)
│   └── Resume/
│       ├── resume_information.csv           (1,013 records, 3.8 MB)
│       └── resumes/                         (1,013 PDF files)
├── IT_Service_Management/
│   └── it_tickets.json                      (163 records, ~100 KB)
├── Policy_Documents/
│   └── *.pdf                                (24 policy documents)
├── Workspace/
│   └── GitHub/
│       └── GitHub.json                      (750 records, 10.1 MB)
├── tasks.jsonl                              (483 tasks, 22 MB)
└── README.md
```

---

## Data Source Detail

### 1. Human Resource Management — `employees.json`

The canonical employee registry. **This is the central entity table** — nearly every other data source references employees via `emp_id`.

| Field | Type | Example |
|---|---|---|
| `emp_id` | string | `"emp_0431"` |
| `Name` | string | `"Raj Patel"` |
| `email` | string | `"raj.patel@inazuma.com"` |
| `category` | string (department) | `"Engineering"` |
| `Level` | string | `"EN14"` (dept prefix + seniority number) |
| `description` / `Description` | string | Long-form bio narrative |
| `Experience` | string | Work history paragraph |
| `skills` | string | Comma-separated skills |
| `DOJ` | string | `"03-01-2012"` (DD-MM-YYYY) |
| `DOL` | string | `"Present"` or DD-MM-YYYY |
| `Salary` | string (numeric) | `"51000"` (no currency symbol) |
| `Age` | string (numeric) | `"28"` |
| `Performance Rating` | string | `"1"` through `"5"` |
| `Marital Status` | string | `"Married"`, `"Single"`, `"Divorced"` |
| `Gender` | string | `"Male"` or `"Female"` |
| Leave fields (6) | string (numeric) | Total/Remaining for Casual, Sick, Vacation |
| `Total Leaves Taken` | string | Aggregate |
| `is_valid` | string | Always `"TRUE"` |
| `reportees` | array of strings | List of emp_ids reporting to this employee |
| `reports_to` | string or null | Manager's emp_id |

**Records:** 1,260  
**Active:** 951 (DOL = "Present"), **Departed:** 309  
**Departments:** Engineering (479), HR (126), Business Development (124), Information Technology (119), BPO (114), Finance (108), Sales (108), Management (82)  
**Org hierarchy:** 52 root managers (reports_to = null), 681 leaf nodes (empty reportees)  

**Data quality issues:**
- All numeric fields stored as strings
- Duplicate field: both `description` (all records) and `Description` (650 records)
- 215 employees have Age/DOJ combinations implying hiring under age 18
- 1 gender mismatch: emp_0566 "Priya Arora" marked Male
- `is_valid` is uniformly TRUE (no filtering value)
- Level codes use dept-prefix pattern (EN, HR, IT, BP, FI, SA, MG) + seniority (09/10/12/14)

---

### 2. Human Resource Management — `resume_information.csv`

| Field | Type | Example |
|---|---|---|
| `resume_id` | UUID | `"e806c13a-ccfb-4e9a-..."` |
| `emp_id` | string | `"emp_0431"` |
| `category` | string | Resume domain category |
| `name` | string | Candidate name |
| `content` | string | Full resume in markdown+JSON |
| `email` | string | `"sameer.wadhawan@inazuma.com"` |
| `created_date` | string | `"2024-10-28"` |
| `file_path` | string | `"resumes/<UUID>.pdf"` |

**Records:** 1,013 (with 1,013 matching PDF files in `resumes/` directory)  
**Coverage:** 1,007 unique emp_ids out of 1,260 employees (80%)  

**Data quality issues:**
- 6 emp_ids appear twice with completely different people (internal employee + external candidate)
- Resume categories (29 total) don't match employee department categories — includes external categories like "Advocates", "Aviation", "Banking"
- All resumes created in a 3-day window (Oct 28–30, 2024), indicating bulk generation
- 253 employees have no resume record

---

### 3. Enterprise Mail System — `emails.json`

| Field | Type | Example |
|---|---|---|
| `email_id` | UUID | `"4226322d-0ea5-..."` |
| `thread_id` | string | `"THR_20241104_d2b538"` |
| `date` | string | `"2012-03-18 06:58:29 IST"` |
| `sender_email` / `sender_name` / `sender_emp_id` | string | Employee details |
| `recipient_email` / `recipient_name` / `recipient_emp_id` | string | Employee details |
| `subject` | string | Email subject |
| `body` | string | Full email body |
| `importance` | string | `"High"` or `"Normal"` |
| `signature` | string | Footer signature block |
| `category` | string | `"INTERNAL"`, `"FOLLOW-UP"`, `"MEETING"`, `"ANNOUNCEMENT"`, `"URGENT"`, `"GENERAL"` |

**Records:** 11,928 emails across 4,417 threads  
**Unique participants:** 500 senders, 500 recipients  
**Date range:** 2012-01-03 to 2022-12-30  
**Thread sizes:** 1–7 emails per thread (average 2.7)  
**Category breakdown:** INTERNAL (5,832), FOLLOW-UP (5,070), MEETING (654), GENERAL (272), ANNOUNCEMENT (37), URGENT (28)  

**Data quality issues — CRITICAL:**
- **Signature mismatch:** 81% of emails have a `signature` belonging to a different employee than the sender
- **Thread date disorder:** 61% of threads have non-chronological email dates (87% of multi-email threads) → cannot rely on dates for thread ordering
- **JSON corruption:** ~90 records have malformed `category` (48) or `importance` (42) fields (embedded newlines/field fragments)
- All thread_ids use date `20241104` regardless of actual email date (dataset generation artifact)

---

### 4. Collaboration Tools — `conversations.json`

| Field | Type | Example |
|---|---|---|
| `conversation_id` | UUID | `"64380325-e9a6-..."` |
| `sender_emp_id` | string | `"emp_0436"` |
| `recipient_emp_id` | string | `"emp_0121"` |
| `date` | string | `"2020-06-07"` |
| `text` | string | Full multi-turn transcript |

**Records:** 2,897 conversations  
**Date range:** 2012-01-03 to 2022-12-29  
**Unique senders:** 649, **Unique recipients:** 396  

**Data quality issues:**
- 34 self-conversations (sender == recipient)
- Inconsistent speaker labeling: some use real names ("Surya Reddy:"), others use generic labels ("Emp1:")
- ~191 conversations feature Western names despite Indian company context (template blending)

---

### 5. Enterprise Social Platform — `posts.json`

| Field | Type | Example |
|---|---|---|
| `Title` | string | `"Leveraging Data for Strategic Growth..."` |
| `Post` | string | Full post body (~1,595 chars avg) |
| `emp_id` | string | `"emp_0604"` |
| `author` | string | `"Hari Kumar"` |

**Records:** 971 posts from 236 unique employees  
**No timestamp field**  
**Content pattern:** Internal social posts commenting on real tech news stories in context of Inazuma.co strategy  

---

### 6. Customer Relation Management — `customers.json`

| Field | Type | Example |
|---|---|---|
| `customer_id` | string (5-char) | `"arout"` |
| `customer_name` | string | `"thomas hardy"` (all lowercase) |
| `invoice_paths` | string | `"Financial System/Customer_orders/invoice_arout.pdf"` |
| `purchase_order_paths` | string | Path to purchase order PDF |
| `shipping_order_paths` | string | Path to shipping order PDF |

**Records:** 90 (based on Northwind database customer IDs)  
**Note:** 1 synthetic placeholder record with `customer_id = "ADDED"`  

---

### 7. Customer Relation Management — `products.json`

| Field | Type | Example |
|---|---|---|
| `product_id` | string (ASIN) | `"B07JW9H4J1"` |
| `product_name` | string | Full product title (80–200 chars) |
| `category` | string | Pipe-delimited hierarchy: `"Electronics\|WearableTechnology\|SmartWatches"` |
| `discounted_price` | string | `"₹399"` |
| `actual_price` | string | `"₹1,099"` |
| `rating` | string | `"4.3"` |
| `about_product` | string | Pipe-delimited bullet points |

**Records:** 1,351 (real Amazon India ASINs)  
**Category top-levels:** Electronics (490), Home&Kitchen (448), Computers&Accessories (375), OfficeProducts (31), others  
**Rating range:** 2.0–5.0 (avg 4.09), 1 corrupted value (`"|"`)  

---

### 8. Customer Relation Management — `sales.json`

| Field | Type | Example |
|---|---|---|
| `product_id` | string | FK → products.json |
| `customer_id` | string | FK → customers.json |
| `discounted_price` | string | `"₹399"` |
| `actual_price` | string | `"₹1,099"` |
| `discount_percentage` | string | `"64%"` |
| `Date_of_Purchase` | string | `"2013-02-06"` |
| `sales_record_id` | integer | 0–13509 |

**Records:** 13,510  
**Pattern:** Exactly 10 sales per product (synthetic)  
**Date range:** 2012–2022 (heavily skewed to 2012 with 5,050 records)  
**Top customers by volume:** savea (477), ernsh (473), quick (451)  

---

### 9. Customer Support — `customer_support_chats.json`

| Field | Type | Example |
|---|---|---|
| `product_id` | string | FK → products.json |
| `product_name` | string | Denormalized |
| `customer_name` | string | Denormalized |
| `customer_id` | string | FK → customers.json |
| `emp_id` | string | Support employee (emp_XXXX) |
| `text` | string | Full chat transcript in markdown |
| `interaction_date` | string | ISO date |
| `chat_id` | integer | 0-indexed |

**Records:** 1,000  
**Support agents:** 27 unique employees  
**Products covered:** 687 of 1,351  

---

### 10. Product Sentiment — `product_sentiment.json`

| Field | Type | Example |
|---|---|---|
| `product_id` | string | FK → products.json |
| `customer_id` | string | FK → customers.json |
| `review_content` | string | Product review text (pipe-delimited) |
| `review_date` | string | ISO date |
| `sentiment_id` | integer | 0-indexed |

**Records:** 13,510 (exact 1:1 with sales.json on (product_id, customer_id) pairs)  

**CRITICAL issue:** All customers who bought the same product share **identical** review text. Only 1,351 distinct review texts exist across 13,510 records. Reviews are product-level, not customer-level.  
**2,150 records** contain embedded Amazon image URLs in review text.  
**No sentiment score** — raw text only despite the "sentiment" filename.

---

### 11. Business & Management — `clients.json`

| Field | Type | Example |
|---|---|---|
| `client_id` | UUID | `"3a578a8e-a948-..."` |
| `business_name` | string | `"Rodriguez, Figueroa and Sanchez"` |
| `industry` | string | 10 sectors |
| `business_type` | string | B2B, B2C, Enterprise, etc. |
| `contact_person_id` | UUID | Contact person identifier |
| `contact_person_name` | string | Contact's full name |
| `contact_email` | string | Contact email |
| `phone_number` | string | US format |
| `registered_address` | string | Full mailing address |
| `tax_id` | string | 7-char alphanumeric |
| `monthly_revenue` | string | `"$2,357,113"` |
| `onboarding_date` | string | ISO date (2022–2025) |
| `current_POC_product` | string | 10 product categories |
| `POC_status` | string | `"ongoing"` or `"accepted"` (exact 50/50) |
| `engagement_description` | string | Free text |
| `business_representative_employee` | string | `"emp_0695"` |

**Records:** 400  

---

### 12. Business & Management — `vendors.json`

| Field | Type | Example |
|---|---|---|
| `client_id` | string | `"CLNT-0001"` (sequential) |
| `business_name` | string | Company name |
| `industry` | string | 10 sectors |
| `business_type` | string | Integration Partner, Reseller, etc. (13 types) |
| `registered_address` | string | Full address |
| `tax_id` | string | 7-char alphanumeric |
| `onboarding_date` | string | ISO date (2023–2025) |
| `relationship_description` | string | Boilerplate by business_type |
| `management_representative_employee` | string | `"emp_0200"` |

**Records:** 400  

**WARNING:** `client_id` field name collides between `clients.json` (UUIDs) and `vendors.json` (CLNT-XXXX) — incompatible formats despite same field name.

---

### 13. IT Service Management — `it_tickets.json`

| Field | Type | Example |
|---|---|---|
| `id` | string (numeric) | `"717"` |
| `priority` | string | `"low"`, `"medium"`, `"high"` |
| `raised_by_emp_id` | string | Employee who submitted |
| `assigned_date` | string | ISO date |
| `emp_id` | string | IT staff assigned |
| `Issue` | string | Full issue description |
| `Resolution` | string | Full resolution response |

**Records:** 163  
**Priority:** low (37), medium (53), high (73)  
**Date range:** 2012–2022  
**Note:** Sparse ID space (163 records across range 717–98,678)  

---

### 14. Workspace — `GitHub.json`

| Field | Type | Example |
|---|---|---|
| `repo_name` | string | `"ahmedbodi/AutobahnPython"` |
| `path` | string | File path in repo |
| `copies` | string (numeric) | Number of known copies |
| `size` | integer | File size in bytes |
| `code` | string | **Full source code content** |
| `license` | string | SPDX identifier |
| `hash` | string | MD5 hash |
| `emp_id` | string | Repository owner |
| `creation_date` | string | ISO date |
| `language` | string | Mostly `"Python"` |
| `issues` | object or null | Embedded issue with id, title, description, status, created_at, patch |

**Records:** 750 (726 unique repos)  
**Languages:** Python (745), SQL (1), reStructuredText (1), others  
**Issue status:** open (657), closed (90), null (3)  
**Owner spread:** 568 unique employees  

---

### 15. Policy Documents

**24 PDF files** covering Inazuma.co corporate policies:

| Category | Policies |
|---|---|
| IT/Security | Acceptable Use, Information Security, Password, IT Asset Management |
| HR/Employee | Employee Handbook, Leave, Performance Management, POSH, Medical Insurance |
| Legal/Compliance | Companies Act, Compliance, Corporate Governance |
| Data/Privacy | Data Protection, Data Breach Response, Privacy Notice |
| Environmental | Ecological Sustainability, Environmental Compliance |
| Development | SDLC, Software Development Standards |
| Financial | Travel & Business Expense Reimbursement |
| Risk | Risk Management, Occupational Health & Safety |
| Ethics | Code of Ethics, Social Media |

---

### 16. Customer Orders (PDFs)

**270 PDF files:** 90 invoices + 90 purchase orders + 90 shipping orders  
Naming: `<type>_<customer_id>.pdf` (e.g., `invoice_arout.pdf`)  
Referenced by: `customers.json` path fields  

---

### 17. Tasks — `tasks.jsonl`

**483 evaluation tasks** (ReAct conversation traces)  
Each task contains: system message (with 62 tool definitions), user prompt, assistant turns, tool calls/results  
**Domain distribution:** HR (222), GitHub/Code (110), CRM/Products (64), Messaging (36), Email (20), IT (8), Other (23)  

---

## Entity Relationship Map

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        EMPLOYEES (emp_id) — 1,260                       │
│                    Central entity hub for all data                       │
└───────┬──────┬──────┬──────┬──────┬──────┬──────┬──────┬───────────────┘
        │      │      │      │      │      │      │      │
        ▼      ▼      ▼      ▼      ▼      ▼      ▼      ▼
   ┌────────┐ ┌───────┐ ┌──────┐ ┌──────┐ ┌─────┐ ┌──────┐ ┌──────┐ ┌────────┐
   │Emails  │ │Convos │ │Posts │ │GitHub│ │IT   │ │Chats │ │Clients│ │Vendors │
   │11,928  │ │2,897  │ │971  │ │750   │ │163  │ │1,000 │ │400   │ │400     │
   │sender/ │ │sender/│ │emp_ │ │emp_  │ │raised│ │emp_  │ │rep_  │ │rep_    │
   │recipnt │ │recipnt│ │id   │ │id    │ │/asgn │ │id    │ │emp   │ │emp     │
   └────────┘ └───────┘ └──────┘ └──────┘ └─────┘ └──┬───┘ └──────┘ └────────┘
                                                      │
        ┌─────────────────────────────────────────────┘
        │
        ▼
   ┌──────────┐     ┌───────────┐     ┌────────────┐
   │Customers │────▶│  Sales    │◀────│ Products   │
   │  90      │     │ 13,510    │     │  1,351     │
   └────┬─────┘     └─────┬─────┘     └──────┬─────┘
        │                 │                   │
        │           ┌─────▼─────┐             │
        └──────────▶│ Sentiment │◀────────────┘
                    │  13,510   │
                    └───────────┘

   ┌─────────────────┐    ┌──────────────────┐
   │  Resumes (CSV)  │    │ Policy Documents │
   │  1,013 + PDFs   │    │    24 PDFs       │
   │  FK: emp_id     │    │  (no FK links)   │
   └─────────────────┘    └──────────────────┘

   ┌──────────────────┐
   │ Customer Orders  │
   │   270 PDFs       │
   │ FK: customer_id  │
   └──────────────────┘
```

---

## Foreign Key Relationship Matrix

| Source File | FK Field | Target | Coverage |
|---|---|---|---|
| employees.json | `reports_to` / `reportees` | employees.json (self) | Internal hierarchy |
| emails.json | `sender_emp_id`, `recipient_emp_id` | employees.json | 500/1,260 (40%) |
| conversations.json | `sender_emp_id`, `recipient_emp_id` | employees.json | 649/1,260 (52%) |
| posts.json | `emp_id` | employees.json | 236/1,260 (19%) |
| GitHub.json | `emp_id` | employees.json | 568/1,260 (45%) |
| it_tickets.json | `raised_by_emp_id`, `emp_id` | employees.json | 154+49/1,260 |
| customer_support_chats.json | `emp_id` | employees.json | 27/1,260 (2%) |
| clients.json | `business_representative_employee` | employees.json | 63 unique |
| vendors.json | `management_representative_employee` | employees.json | 45 unique |
| resume_information.csv | `emp_id` | employees.json | 1,007/1,260 (80%) |
| sales.json | `customer_id` | customers.json | 90/90 (100%) |
| sales.json | `product_id` | products.json | 1,351/1,351 (100%) |
| product_sentiment.json | `customer_id` | customers.json | 90/90 (100%) |
| product_sentiment.json | `product_id` | products.json | 1,351/1,351 (100%) |
| customer_support_chats.json | `customer_id` | customers.json | 88/90 (98%) |
| customer_support_chats.json | `product_id` | products.json | 687/1,351 (51%) |
| customers.json | `*_paths` | Customer_orders/ PDFs | 90/90 (100%) |

---

## Record Count Summary

| Data Source | Records | Primary Key |
|---|---|---|
| employees.json | 1,260 | `emp_id` |
| resume_information.csv | 1,013 | `resume_id` |
| Resume PDFs | 1,013 files | filename = UUID |
| emails.json | 11,928 | `email_id` |
| conversations.json | 2,897 | `conversation_id` |
| posts.json | 971 | (no explicit PK) |
| customers.json | 90 | `customer_id` |
| products.json | 1,351 | `product_id` |
| sales.json | 13,510 | `sales_record_id` |
| product_sentiment.json | 13,510 | `sentiment_id` |
| customer_support_chats.json | 1,000 | `chat_id` |
| clients.json | 400 | `client_id` (UUID) |
| vendors.json | 400 | `client_id` (CLNT-XXXX) |
| it_tickets.json | 163 | `id` |
| GitHub.json | 750 | `repo_name` + `path` |
| Policy PDFs | 24 files | filename |
| Customer Order PDFs | 270 files | filename |
| **Total records** | **~49,755** | |
| **Total files (incl. PDFs)** | **~1,307 files** | |

---

## Critical Data Quality Issues to Handle During Ingestion

### High Severity
1. **Email signature mismatch** — 81% of emails have signatures from wrong employees → must not extract sender identity from signature field
2. **Thread date disorder** — 61% of email threads are non-chronological → cannot rely on dates for thread ordering
3. **JSON corruption in emails** — ~90 records with malformed fields → need error-tolerant parsing
4. **Duplicate emp_ids in resumes** — 6 IDs map to two different people → need deduplication logic
5. **Product sentiment is product-level, not customer-level** — identical review text for all buyers of same product → don't model as individual customer opinions

### Medium Severity
6. **All numeric fields stored as strings** across all JSON files → type casting needed everywhere
7. **Price strings require parsing** (strip ₹, commas, % symbols)
8. **Date format inconsistency**: DD-MM-YYYY in employees, YYYY-MM-DD in most others, datetime+timezone in emails
9. **`client_id` field name collision** between clients.json (UUID) and vendors.json (CLNT-XXXX)
10. **Zero employee ID overlap** across support chats, clients, and vendors despite shared emp_XXXX format
11. **"ADDED" placeholder customer** appears in customers, sales, sentiment but not support chats

### Low Severity / Informational
12. **Synthetic patterns**: exactly 10 sales per product, 50/50 POC_status split, uniform leave allocations
13. **All customer names lowercase** — inconsistent with typical CRM
14. **No timestamps on social posts** — cannot determine posting order
15. **Category pipe delimiter** same as bullet delimiter in products → caused 1 rating corruption
16. **Western names mixed into Indian company data** (conversations, some posts)

---

## Key Entities for Knowledge Graph Construction

Based on this analysis, the primary entities to extract for the context base are:

| Entity Type | Source(s) | Count | Key Attributes |
|---|---|---|---|
| **Employee** | employees.json, resumes | 1,260 | name, email, dept, level, skills, manager, salary, status |
| **Customer** | customers.json | 90 | id, name, order documents |
| **Product** | products.json | 1,351 | id, name, category tree, price, rating |
| **Client (B2B)** | clients.json | 400 | company, industry, POC product, revenue, rep |
| **Vendor/Partner** | vendors.json | 400 | company, industry, relationship type, rep |
| **Email Thread** | emails.json | 4,417 | participants, subject, dates, importance |
| **Conversation** | conversations.json | 2,897 | participants, date, content |
| **IT Ticket** | it_tickets.json | 163 | priority, raiser, assignee, issue, resolution |
| **Code Repository** | GitHub.json | 726 | name, owner, language, license, issues |
| **Policy** | Policy_Documents/ | 24 | category, title, full text |
| **Sale Transaction** | sales.json | 13,510 | product, customer, price, date |
| **Support Chat** | customer_support_chats.json | 1,000 | product, customer, agent, transcript |
| **Social Post** | posts.json | 971 | author, title, content |
| **Resume** | resume_information.csv | 1,013 | employee, skills, experience |

### Key Relationships to Model

| Relationship | From → To | Source |
|---|---|---|
| `REPORTS_TO` | Employee → Employee | employees.json (reports_to) |
| `MANAGES` | Employee → Employee | employees.json (reportees) |
| `SENT_EMAIL` | Employee → Email | emails.json (sender_emp_id) |
| `RECEIVED_EMAIL` | Employee → Email | emails.json (recipient_emp_id) |
| `EMAIL_IN_THREAD` | Email → Thread | emails.json (thread_id) |
| `PARTICIPATED_IN_CONVERSATION` | Employee → Conversation | conversations.json |
| `AUTHORED_POST` | Employee → Post | posts.json |
| `OWNS_REPO` | Employee → Repository | GitHub.json |
| `RAISED_TICKET` | Employee → IT Ticket | it_tickets.json (raised_by) |
| `ASSIGNED_TICKET` | Employee → IT Ticket | it_tickets.json (emp_id) |
| `HANDLES_SUPPORT` | Employee → Support Chat | customer_support_chats.json |
| `REPRESENTS_CLIENT` | Employee → Client | clients.json |
| `MANAGES_VENDOR` | Employee → Vendor | vendors.json |
| `PURCHASED` | Customer → Product | sales.json |
| `REVIEWED` | Customer → Product | product_sentiment.json |
| `CONTACTED_SUPPORT_ABOUT` | Customer → Product | customer_support_chats.json |
| `HAS_INVOICE` / `HAS_PO` / `HAS_SO` | Customer → Document | customers.json paths |
| `BELONGS_TO_CATEGORY` | Product → Category | products.json (pipe-delimited) |
| `IN_INDUSTRY` | Client/Vendor → Industry | clients/vendors.json |
| `POC_FOR_PRODUCT` | Client → POC Product | clients.json |
