# AI-assisted development and contribution policy

This policy describes the project's review process for AI-assisted work. [LICENSE](LICENSE) governs use and distribution; the [Contributor License Agreement](CONTRIBUTOR_LICENSE_AGREEMENT.md) governs accepted contributions. This policy creates no additional license grant or warranty of ownership.

## Contributor responsibilities

AI tools may assist development, documentation, tests and design. The submitting human remains responsible for understanding and reviewing the contribution, testing its behavior, and having the authority required by the CLA. Tool-generated output is not evidence of originality, security, copyright ownership or permission to incorporate third-party material.

In a pull request, disclose material AI assistance and describe what you reviewed and tested. Record the tool/model when known, the affected areas, and any third-party source, copied snippet, asset or dependency that influenced the result. Do not invent a model name or reconstruct a provenance record you do not have. Full private prompts are not required.

Check the tool/provider terms that applied when the work was produced. Review recognizable upstream code and assets against their actual licenses and preserve required notices. Generated code does not become exempt from third-party obligations. If you cannot establish permission for included material, remove or replace it before submission.

## Human review before merge or release

Reviewers should inspect correctness and failure paths, authentication and authorization, secret handling, input validation, dependencies, and relevant platform behavior. Record tests actually run and any remaining gaps. AI review may supplement this work; it does not substitute for the responsible maintainer's release decision.

Do not send credentials, private keys, customer data, private vulnerability details or confidential third-party code to an AI service without the authority to do so. Use synthetic fixtures and redact sensitive information from prompts and pull requests.

## Existing material and corrections

This policy does not certify the provenance of every historical line. If a licensing or attribution issue is discovered, preserve the available evidence, notify the maintainer through the appropriate project channel, and replace or correct the affected material and notices. Use [SECURITY.md](SECURITY.md) for security-sensitive reports rather than publishing exploit details in a general issue.
