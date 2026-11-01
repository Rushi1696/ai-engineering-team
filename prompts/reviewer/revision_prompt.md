---
version: 1.0.0
last_modified: 2026-10-31
author: Reviewer Engineering Team
---
Your previous code review output failed validation:
{error_message}

Project Name: {project_name}

Product Requirements:
{requirements}

System Architecture:
{architecture}

Static Analysis Findings:
{static_analysis}

Test Coverage Assessment:
{coverage_report}

Code Artifacts:
{code_artifacts}

Test Artifacts:
{test_artifacts}

Please strictly correct the validation issues:
- If overall_status is "rejected", rejection_scope MUST be one of "code_level", "design_level", "requirements_level".
- If overall_status is "approved" or "requires_changes", rejection_scope MUST be null.
- code_quality_score MUST be between 0.0 and 10.0.
Return a valid ReviewerOutput.
