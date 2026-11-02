---
version: 1.0.0
last_modified: 2026-11-02
author: DevOps Engineering Team
---
Your previous deployment artifacts generation failed validation:
{error_message}

Project Name: {project_name}

System Architecture:
{architecture}

Code Artifacts:
{code_artifacts}

Please correct the issues:
- You must generate at least one artifact with artifact_type "dockerfile" and at least one with "ci_cd_pipeline".
- Ensure file_path and content are non-empty and syntactically valid.
