---
version: 1.0.0
last_modified: 2026-10-20
author: Prompt Engineering Team
---
Your previous task plan output failed validation:
{error_message}

Project Name: {project_name}

Product Requirements:
{requirements}

System Architecture:
{architecture}

User Context:
{user_context}

Please strictly correct the validation issues and return a valid TaskPlan where:
1. Every task has a unique task_id, non-empty title/description, assigned_agent in ["BackendAgent", "FrontendAgent", "QAAgent"], priority in 1..100, and non-empty acceptance_criteria.
2. All dependencies reference valid task_id values in the same plan.
3. execution_order contains each task_id exactly once in strict topological order (no task precedes its dependencies).
