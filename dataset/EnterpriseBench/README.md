---
language:
- en
license: mit
task_categories:
- question-answering
- text-generation
pretty_name: EnterpriseBench
size_categories:
- n<1K
tags:
- enterprise
- agent-evaluation
- tool-calling
- benchmark
- llm-evaluation
configs:
- config_name: default
  data_files: tasks.jsonl
---


# Can LLMs Help You at Work? A Sandbox for Evaluating LLM Agents in Enterprise Environments

## Dataset Description

EnterpriseBench is a comprehensive sandbox for evaluating Large Language Model (LLM) agents in realistic enterprise environments. This dataset provides structured enterprise data across multiple business domains, along with evaluation tasks for assessing agent capabilities in workplace scenarios.

### Dataset Summary

EnterpriseBench includes:
- **Enterprise data** across multiple business domains
- **Evaluation tasks** defined in `tasks.json`
- **Realistic scenarios** for testing LLM agents in workplace settings
- **Multi-domain coverage** including HR, IT, customer relations, and more

## Dataset Structure

The dataset is organized into the following directories:

- `Business_and_Management/` - Business management data and documents
- `Enterprise_mail_system/` - Email system data and communications
- `Inazuma_Overflow/` - Technical Q&A and knowledge base
- `IT_Service_Management/` - IT service tickets and management data
- `Workspace/` - General workspace and collaboration data
- `Collaboration_tools/` - Team collaboration and project data
- `Customer_Relation_Management/` - CRM data and customer interactions
- `Enterprise Social Platform/` - Internal social platform data
- `Human_Resource_Management/` - HR records and employee data
- `Policy_Documents/` - Company policies and procedures
- `tasks.json` - Evaluation task definitions and metadata

### Data Files

The main evaluation tasks are defined in `tasks.json`, which contains:
- Task descriptions
- Expected inputs and outputs
- Evaluation criteria
- Domain-specific requirements

## Usage

### Loading the Dataset

from datasets import load_dataset

Load the task definitions
dataset = load_dataset("AST-FRI/EnterpriseBench", data_files="tasks.json")

Access tasks
tasks = dataset['train']



### Example Use Case

Evaluate an LLM agent on enterprise tasks
import json

Load tasks
with open("tasks.json", "r") as f:
tasks = json.load(f)

Iterate through evaluation tasks
for task in tasks:
# Your agent evaluation code here
pass



## Dataset Creation

### Curation Rationale

EnterpriseBench was created to provide a standardized benchmark for evaluating LLM agents in realistic enterprise scenarios. Traditional benchmarks often lack the complexity and domain-specific requirements of real workplace environments.

### Source Data

The dataset was curated to represent typical enterprise data structures and workflows, including:
- Employee records and HR data
- Customer service interactions
- IT support tickets
- Business documentation
- Internal communications

## Considerations for Using the Data

### Social Impact

This dataset is designed for research and evaluation purposes. Users should be aware that enterprise scenarios may contain sensitive information patterns and should ensure appropriate data handling practices.

### Limitations

- The dataset represents simulated enterprise environments
- Real-world enterprise data may have additional complexity
- Performance on this benchmark may not fully reflect real-world deployment scenarios

## 📝 Citation

If you use EnterpriseBench in your research, please cite our paper:

```bibtex
@inproceedings{vishwakarma-etal-2025-llms,
    title = "Can {LLM}s Help You at Work? A Sandbox for Evaluating {LLM} Agents in Enterprise Environments",
    author = "Vishwakarma, Harsh  and
      Agarwal, Ankush  and
      Patil, Ojas  and
      Devaguptapu, Chaitanya  and
      Chandran, Mahesh",
    editor = "Christodoulopoulos, Christos  and
      Chakraborty, Tanmoy  and
      Rose, Carolyn  and
      Peng, Violet",
    booktitle = "Proceedings of the 2025 Conference on Empirical Methods in Natural Language Processing",
    month = nov,
    year = "2025",
    address = "Suzhou, China",
    publisher = "Association for Computational Linguistics",
    url = "https://aclanthology.org/2025.emnlp-main.466/",
    pages = "9178--9212",
    ISBN = "979-8-89176-332-6",
}
```



## Dataset Card Authors

Harsh Vishwakarma, Ankush Agarwal, Ojas F Patil, Chaitanya Devaguptapu, Mahesh Chandran

## License

This dataset is released under the MIT License.