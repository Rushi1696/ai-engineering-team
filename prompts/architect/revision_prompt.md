---
version: 1.0.0
last_modified: 2026-10-19
author: Prompt Engineering Team
---
Your previous system architecture output failed validation:
{error_message}

Project Name: {project_name}

Product Requirements:
{requirements}

User Context:
{user_context}

Please strictly correct the validation issues and return a valid SystemArchitecture document with:
1. Non-empty 'overview', 'scalability_strategy', and 'deployment_model' strings.
2. A non-empty 'technology_stack' dictionary mapping components to technologies.
3. Valid 'api_specifications' (each containing 'endpoint' and 'method').
4. Valid 'database_design' and 'security_architecture' dictionaries.
