# JSON API Forge exampleApps v0.5.2

**Release candidate date:** 11 October 2026
**Status:** Alpha reference configurations

This branch distributes 25 copy-ready v0.5.2 projects and their installation/generation tooling. It contains no Forge runtime; consumers copy selected projects into an independently obtained matching `main` checkout.

v0.5.2 adds atomic imports in TaskBoard and the 20 generated domain apps, a nullable-date cursor feed, a bounded async SDK recipe and real-server rollback/permissions checks.

The catalog spans task management, transactional ledgers, realtime support, media, public data and 20 substantial domain workflows. Configurations are synthetic examples, not professional advice or deployment-specific production policy.

CI validates all projects with the real `main` runtime on Python 3.10–3.14, tests Bash and PowerShell installers on native hosts, checks branch purity and CodeQL, and uploads a deterministic ZIP plus SHA-256 checksum. It never publishes a GitHub Release automatically.

JSON API Forge is source-available, not OSI open source. Official publication authority remains with Cavanşir Qurbanzadə (`@Cavanshirpro`) or a lawful successor/assignee.
