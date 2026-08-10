# LoadRunner Enterprise 26 R1 — MVP Platform

## Comprehensive Product, Architecture, Frontend, Backend, Database & DevOps Specification

**Document Type:** Technical Product Requirements & Architecture Specification
**Target:** MVP / Production-oriented Proof of Concept
**Product:** Enterprise Performance Engineering / Load Testing Platform
**Reference Product:** OpenText LoadRunner Enterprise 26 R1
**Implementation:** Independent implementation inspired by LRE capabilities
**Version:** 1.0
**Date:** 2026-08-10

---

# 1. Executive Summary

This document specifies a full-stack MVP for an enterprise performance-testing platform modeled around the major workflows of LoadRunner Enterprise.

The platform provides:

* Multi-tenant/project-based performance testing
* User and role management
* Performance-test planning
* Script management
* Load-generator management
* Test execution
* Virtual-user/workload modeling
* Runtime monitoring
* Transaction measurements
* Infrastructure metrics
* Test-result storage
* Real-time dashboards
* Analysis and reporting
* Baseline comparison
* Thresholds and SLA validation
* Test scheduling
* CI/CD integration
* REST API
* Audit logging
* Notifications
* Resource/licensing management
* Extensible protocol/executor architecture

The MVP should not attempt to reproduce every proprietary protocol and feature of commercial LRE.

Instead, it should establish a scalable architecture capable of supporting:

```text
Users
  |
  v
Web Application
  |
  v
API Gateway
  |
  +--------------------+
  |                    |
  v                    v
Identity Service    Core API
                       |
          +------------+-------------+
          |            |             |
          v            v             v
      Test Mgmt    Execution     Reporting
          |            |             |
          v            v             v
       PostgreSQL   Message Bus   Analytics DB
                       |
             +---------+---------+
             |         |         |
             v         v         v
          Workers   Load Gen   Monitors
```

---

# 2. Product Vision

Build a centralized enterprise platform where performance engineers can:

1. Create projects.
2. Upload or create performance scripts.
3. Register load generators.
4. Define performance tests.
5. Model virtual-user workloads.
6. Select load generators.
7. Execute tests.
8. Observe execution in real time.
9. Collect application and infrastructure metrics.
10. Analyze transactions and response times.
11. Compare results against baselines.
12. Generate reports.
13. Automate tests through CI/CD.

The platform should provide a single control plane for performance engineering.

---

# 3. Product Positioning

The reference architecture is based on the core philosophy of LoadRunner Enterprise:

* Centralized performance testing
* Shared testing resources
* Distributed execution
* Multiple concurrent tests
* Reusable test assets
* Enterprise reporting
* Web-based administration
* Integration with CI/CD
* Load-generator orchestration

OpenText describes LRE as a collaborative, centralized performance-testing platform designed for distributed teams and shared infrastructure.

The MVP therefore prioritizes **central orchestration over individual load-generation technology**.

---

# 4. MVP Goals

## 4.1 Primary Goals

The MVP must support the complete lifecycle:

```text
Create Project
      |
      v
Create/Upload Script
      |
      v
Register Load Generator
      |
      v
Create Performance Test
      |
      v
Define Workload
      |
      v
Schedule / Start Test
      |
      v
Load Generator Executes
      |
      v
Metrics Stream
      |
      v
Real-Time Dashboard
      |
      v
Test Completes
      |
      v
Analyze Results
      |
      v
Generate Report
      |
      v
Compare Against Baseline
```

## 4.2 Secondary Goals

* Enterprise-grade authentication
* RBAC
* Auditability
* Horizontal scalability
* Containerized deployment
* API-first architecture
* Extensible executor model
* Extensible monitoring model
* CI/CD automation
* Reproducible local development

---

# 5. Non-Goals for MVP

The following should be architecturally possible but not necessarily fully implemented initially:

* Every LoadRunner protocol
* Full VuGen replacement
* Native proprietary LoadRunner script execution
* Full Service Virtualization
* AI-based script generation
* Network virtualization
* Advanced topology discovery
* Global distributed cloud execution
* Commercial licensing management
* Proprietary OpenText integrations
* Full enterprise SSO catalog
* Massive-scale millions-of-users execution

These belong to later releases.

---

# 6. Core Functional Modules

The application consists of the following modules.

```text
1. Authentication
2. User Management
3. Organization Management
4. Project Management
5. Script Management
6. Script Repository
7. Performance Test Management
8. Workload Modeling
9. Load Generator Management
10. Test Scheduling
11. Test Execution
12. Runtime Monitoring
13. Metrics Collection
14. Transaction Analytics
15. Results Repository
16. Baseline Management
17. SLA / Threshold Management
18. Reports
19. Dashboard
20. Notifications
21. Audit Logs
22. API
23. CI/CD Integration
24. System Administration
```

---

# 7. High-Level Architecture

## 7.1 Recommended Architecture

Use a **modular monolith for the MVP**, with independently deployable execution workers.

Do not start with 20 microservices.

Recommended:

```text
                       Internet / Corporate Network
                                  |
                                  v
                         +----------------+
                         | Load Balancer  |
                         +-------+--------+
                                 |
                                 v
                         +----------------+
                         | Web Frontend  |
                         | React/Next.js |
                         +-------+--------+
                                 |
                                 v
                         +----------------+
                         | API Backend    |
                         | FastAPI/NestJS |
                         +-------+--------+
                                 |
             +-------------------+-------------------+
             |                   |                   |
             v                   v                   v
       PostgreSQL           Redis             Object Storage
       Metadata             Cache             Scripts/Results
             |                   |                   |
             +-------------------+-------------------+
                                 |
                                 v
                          Message Broker
                          Kafka/RabbitMQ
                                 |
                    +------------+-------------+
                    |            |             |
                    v            v             v
               Scheduler     Executor      Metrics
                 Worker       Worker        Worker
                    |            |
                    |            v
                    |      Load Generator
                    |            |
                    |            v
                    |       Target App
                    |
                    v
                PostgreSQL
                    +
                TimescaleDB
```

---

# 8. Technology Stack

## 8.1 Frontend

Recommended:

* React
* TypeScript
* Next.js
* Tailwind CSS
* shadcn/ui
* TanStack Query
* TanStack Table
* React Hook Form
* Zod
* Recharts
* ECharts
* Monaco Editor
* WebSocket client

Alternative:

```text
React + Vite
```

if server-side rendering is unnecessary.

---

# 9. Backend Technology

Recommended:

```text
Python 3.12+
FastAPI
SQLAlchemy
Alembic
Pydantic
Celery / Dramatiq
Redis
```

Alternative:

```text
Node.js
NestJS
TypeScript
Prisma
BullMQ
```

Python is preferred because performance-engineering ecosystems commonly benefit from Python's HTTP, metrics, automation, and data-analysis libraries.

---

# 10. Databases

## 10.1 Primary Database

PostgreSQL.

Responsibilities:

* Users
* Organizations
* Projects
* Tests
* Scripts
* Test configurations
* Load generators
* Schedules
* Permissions
* Baselines
* Thresholds
* Test runs
* Metadata
* Audit records

---

# 11. Time-Series Database

Use:

```text
TimescaleDB
```

on top of PostgreSQL for the MVP.

Alternative:

```text
InfluxDB
```

OpenText training material specifically references Grafana and InfluxDB in advanced LRE workflows, so the architecture should keep time-series metrics separated from transactional application data.

---

# 12. Object Storage

Use S3-compatible object storage.

Development:

```text
MinIO
```

Production:

```text
AWS S3
Azure Blob Storage
Google Cloud Storage
```

Store:

* Script archives
* Uploaded files
* Result artifacts
* Logs
* Reports
* Screenshots
* Raw metric exports
* JTL/CSV/JSON result files

---

# 13. Cache

Redis.

Use for:

* Session/cache data
* Distributed locks
* Job state
* Temporary execution state
* WebSocket fan-out
* Rate limiting
* Scheduler locks

---

# 14. Message Broker

Recommended:

```text
RabbitMQ
```

or:

```text
Kafka
```

### MVP recommendation

Use RabbitMQ.

Queues:

```text
test.execution
test.stop
test.cancel
load-generator.commands
metrics.ingestion
reports.generation
notifications
```

Kafka becomes preferable when metric/event volume becomes extremely large.

---

# 15. Load Generator Architecture

A critical architectural requirement is to separate the **control plane** from the **execution plane**.

## Control Plane

Responsible for:

* Test configuration
* Scheduling
* Authentication
* Resource allocation
* Execution orchestration
* Result management

## Execution Plane

Responsible for:

* Running virtual users
* Generating requests
* Measuring response times
* Collecting transaction results
* Sending heartbeats
* Reporting execution state

Architecture:

```text
                 Control Plane
                      |
                 Test Executor
                      |
                Message Broker
                      |
        +-------------+-------------+
        |             |             |
        v             v             v
      LG-01         LG-02         LG-03
        |             |             |
        v             v             v
      VUs           VUs           VUs
        |             |             |
        +-------------+-------------+
                      |
                 Target System
```

---

# 16. Load Generator Agent

Each load generator runs an agent.

Example:

```text
lre-agent
```

Responsibilities:

* Register with controller
* Maintain heartbeat
* Receive test assignments
* Download scripts
* Start execution
* Stop execution
* Report CPU/memory
* Stream execution metrics
* Upload result artifacts
* Report failures

Agent states:

```text
REGISTERING
ONLINE
AVAILABLE
ALLOCATED
RUNNING
STOPPING
ERROR
OFFLINE
MAINTENANCE
```

---

# 17. Agent Communication

Use secure outbound communication:

```text
Load Generator
      |
      | TLS
      v
Controller
```

Prefer WebSocket or gRPC for persistent communication.

Fallback:

```text
HTTPS polling
```

for environments where inbound connectivity is impossible.

---

# 18. Agent Registration

The administrator creates an agent token.

Example:

```text
POST /api/v1/load-generators/register
```

Request:

```json
{
  "name": "lg-prod-01",
  "hostname": "10.10.20.10",
  "region": "eu-west",
  "capacity": 5000
}
```

The agent subsequently sends:

```text
POST /heartbeat
```

every 10 seconds.

---

# 19. Frontend Information Architecture

Main navigation:

```text
Dashboard

Workspace
├── Projects
├── Requirements
├── Scripts
├── Performance Tests
├── Runs
└── Results

Infrastructure
├── Load Generators
├── Hosts
├── Monitors
└── Resource Pools

Analytics
├── Dashboards
├── Reports
├── Baselines
└── Comparisons

Administration
├── Users
├── Roles
├── Projects
├── API Keys
├── Integrations
├── Notifications
└── Audit Logs
```

---

# 20. Global UI Layout

```text
+------------------------------------------------------+
| Logo | Project Selector | Search | Notifications | U |
+------------------------------------------------------+
| Sidebar              |                              |
|                      |                              |
| Dashboard            |       Main Content           |
| Projects             |                              |
| Scripts              |                              |
| Performance Tests    |                              |
| Runs                 |                              |
| Results              |                              |
| Reports              |                              |
| Load Generators      |                              |
| Administration       |                              |
|                      |                              |
+----------------------+------------------------------+
```

---

# 21. Design System

Use a professional enterprise UI.

Colors:

```text
Primary:       #2563EB
Success:       #16A34A
Warning:       #F59E0B
Danger:        #DC2626
Info:          #0891B2
Background:    #F8FAFC
Surface:       #FFFFFF
Border:        #E2E8F0
Text:          #0F172A
Muted:         #64748B
```

Dark mode should be supported.

---

# 22. Dashboard

Dashboard should show:

```text
+------------------------------------------------------+
| Active Tests       | Load Generators | Projects      |
|       3            |      12/15      |      28       |
+------------------------------------------------------+

+------------------------------------------------------+
| Active Test Runs                                     |
|                                                      |
| Test          Users    TPS     Avg RT      Status    |
| Checkout      2,000    421     320ms       RUNNING  |
| API Load      500      812     98ms        RUNNING  |
+------------------------------------------------------+

+------------------------+-----------------------------+
| Response Time          | Throughput                  |
|      chart             |       chart                |
+------------------------+-----------------------------+

+------------------------------------------------------+
| Recent Test Results                                   |
+------------------------------------------------------+
```

---

# 23. Project Management

A project represents an application/team/testing domain.

Project fields:

```text
id
name
description
key
owner
status
environment
created_at
updated_at
```

Example:

```text
E-COMMERCE
BANKING
CRM
PAYMENTS
```

---

# 24. Project Page

Tabs:

```text
Overview
Scripts
Tests
Runs
Results
Reports
Members
Settings
```

---

# 25. Script Management

The MVP supports:

### Script types

```text
HTTP
REST
GraphQL
WebSocket
Browser/API
Gatling
JMeter
k6
Custom Executor
```

The first native executor should be HTTP/REST.

---

# 26. Script Repository

Script metadata:

```text
id
project_id
name
description
protocol
version
status
created_by
updated_by
object_storage_key
checksum
created_at
updated_at
```

Versions:

```text
Script
 |
 +-- v1
 +-- v2
 +-- v3
```

Every execution references an immutable script version.

---

# 27. Script Editor

Use Monaco Editor.

Example:

```javascript
export default async function test(context) {
    const response = await context.http.get(
        "/api/products"
    );

    context.transaction("Get Products", async () => {
        await context.http.get("/api/products");
    });
}
```

The exact scripting language is implementation-defined.

The platform should abstract execution through an executor SDK.

---

# 28. Executor SDK

Create:

```text
Executor
```

interface.

Conceptually:

```typescript
interface Executor {
  validate(script): ValidationResult;
  prepare(run): Promise<void>;
  start(run): Promise<void>;
  stop(run): Promise<void>;
  collectMetrics(): Promise<Metrics>;
}
```

Executors:

```text
HttpExecutor
JMeterExecutor
K6Executor
GatlingExecutor
CustomExecutor
```

---

# 29. Performance Test

A performance test combines:

```text
Scripts
+
Workload
+
Load Generators
+
Duration
+
Ramp-up
+
Ramp-down
+
Thresholds
+
Environment
```

Example:

```text
Test: Checkout Peak Load

Scripts:
  Login
  Browse
  AddToCart
  Checkout

Workload:
  5,000 virtual users

Duration:
  30 minutes

Ramp-up:
  10 minutes

Load Generators:
  LG-01
  LG-02
  LG-03
```

---

# 30. Performance Test Designer

Wizard:

```text
Step 1: General
Step 2: Scripts
Step 3: Workload
Step 4: Load Generators
Step 5: Schedule
Step 6: SLA
Step 7: Review
Step 8: Execute
```

---

# 31. Workload Model

Support:

```text
Virtual Users
Percentage
Absolute Users
Throughput
Arrival Rate
```

MVP minimum:

```text
Virtual Users
Percentage
```

Example:

```text
Login              10%
Browse              50%
Add to Cart         25%
Checkout            15%
```

Total:

```text
100%
```

---

# 32. Load Distribution

Example:

```text
Total VUs: 5000

LG-01: 1500
LG-02: 1500
LG-03: 1000
LG-04: 1000
```

Distribution algorithms:

```text
Even
Weighted
Manual
Capacity-based
```

---

# 33. Runtime Test State Machine

```text
DRAFT
 |
 v
READY
 |
 v
SCHEDULED
 |
 v
ALLOCATING
 |
 v
STARTING
 |
 v
RUNNING
 |
 +----> STOPPING
 |
 v
COMPLETED

Failure:
RUNNING -> FAILED

Cancellation:
RUNNING -> CANCELLED
```

---

# 34. Test Run

Every execution creates an immutable run.

Example:

```text
Test:
Checkout Peak

Run:
RUN-2026-000123

Version:
Test v7

Script versions:
Login v4
Browse v9
Checkout v3

Started:
10:00

Ended:
10:30
```

This ensures reproducibility.

---

# 35. Run Detail Page

Sections:

```text
Overview
Live Metrics
Transactions
Virtual Users
Load Generators
Errors
Infrastructure
Logs
Timeline
Configuration
Artifacts
```

---

# 36. Real-Time Dashboard

Use WebSockets.

Example metrics:

```text
Active Users
Requests/sec
Transactions/sec
Average Response Time
P50
P90
P95
P99
Error Rate
HTTP Errors
Timeouts
Throughput
```

---

# 37. Charts

Required charts:

### Response Time

```text
Time -> Response Time
```

### Throughput

```text
Time -> Requests/sec
```

### Active Users

```text
Time -> VUs
```

### Error Rate

```text
Time -> %
```

### Percentiles

```text
P50
P75
P90
P95
P99
```

---

# 38. Transaction Model

Every transaction produces:

```text
transaction_id
run_id
script_id
name
start_time
end_time
duration_ms
status
error_code
error_message
load_generator_id
virtual_user_id
```

Example:

```text
Checkout
duration = 840ms
status = PASS
```

---

# 39. Raw Request Metrics

Store:

```text
timestamp
run_id
transaction_id
method
url
status_code
duration_ms
request_bytes
response_bytes
error
```

Raw high-cardinality data should have configurable retention.

---

# 40. Metrics Pipeline

```text
Load Generator
      |
      v
Agent
      |
      v
Metrics Collector
      |
      v
Message Broker
      |
      +----------+
      |          |
      v          v
TimescaleDB   Redis
      |
      v
Analytics API
      |
      v
WebSocket
      |
      v
Frontend
```

---

# 41. Metrics Aggregation

Do not write every metric directly into PostgreSQL.

Aggregate into time windows:

```text
1 second
5 seconds
10 seconds
30 seconds
1 minute
```

Example:

```text
metric_min
metric_max
metric_avg
metric_p50
metric_p90
metric_p95
metric_p99
count
error_count
```

---

# 42. Infrastructure Monitoring

Monitor:

```text
CPU
Memory
Disk
Network
Process
Container
```

Optional integrations:

```text
Prometheus
Grafana
OpenTelemetry
Azure Monitor
AWS CloudWatch
Dynatrace
```

LRE supports integrations with infrastructure-monitoring ecosystems, and OpenText documentation specifically references Azure Monitor, Dynatrace and Grafana/InfluxDB-related workflows.

---

# 43. OpenTelemetry

Use OpenTelemetry as the preferred standard.

Architecture:

```text
Application
   |
OpenTelemetry
   |
   +--> Metrics
   +--> Traces
   +--> Logs
```

The platform can correlate:

```text
Load Test
    |
Transaction
    |
Trace
    |
Service
    |
Database
```

---

# 44. SLA / Threshold Engine

Users define rules.

Example:

```text
Login P95 < 1000ms
Checkout P95 < 2000ms
Error Rate < 1%
Throughput > 500 TPS
```

Rule:

```text
metric
operator
threshold
window
severity
```

Example:

```json
{
  "metric": "transaction.p95",
  "transaction": "Checkout",
  "operator": "<=",
  "value": 2000,
  "unit": "ms"
}
```

---

# 45. Pass/Fail Engine

At test completion:

```text
Run Metrics
      |
      v
Threshold Engine
      |
      +--> PASS
      |
      +--> WARNING
      |
      +--> FAIL
```

Example:

```text
Checkout P95: 1840 ms
Limit:         2000 ms

PASS
```

---

# 46. Baselines

Users can mark a completed test as a baseline.

Example:

```text
Baseline:
Release 2.1

P95 Checkout:
1820ms
```

Future run:

```text
Release 2.2

P95 Checkout:
2150ms
```

Regression:

```text
+18.1%
```

---

# 47. Baseline Comparison

Comparison UI:

```text
Metric            Baseline      Current      Change

Checkout P95      1820ms        2150ms       +18.1%
Login P95          650ms         610ms        -6.2%
Error Rate          0.4%          1.2%       +200%
Throughput          810           790        -2.5%
```

Highlight regressions in red.

---

# 48. Reports

Supported report types:

```text
Executive Summary
Performance Summary
Transaction Report
Errors Report
Load Generator Report
Infrastructure Report
SLA Report
Baseline Comparison
Full Test Report
```

Export:

```text
PDF
CSV
JSON
HTML
```

---

# 49. Report Generator

Architecture:

```text
Run
 |
 v
Report Service
 |
 +--> Query metrics
 +--> Query transactions
 +--> Evaluate SLA
 +--> Generate charts
 +--> Render HTML
 +--> Convert PDF
 |
 v
Object Storage
```

---

# 50. Scheduling

Schedule types:

```text
One-time
Daily
Weekly
Cron
CI-triggered
API-triggered
```

Example:

```text
Every weekday at 23:00
```

Scheduler:

```text
Scheduler Worker
      |
      v
Find due tests
      |
      v
Create Run
      |
      v
Execution Queue
```

---

# 51. CI/CD

Expose API endpoints.

Example:

```text
POST /api/v1/tests/{testId}/runs
```

CI pipeline:

```text
Git Push
   |
   v
Build
   |
   v
Deploy
   |
   v
Trigger Performance Test
   |
   v
Wait
   |
   v
Evaluate SLA
   |
   +---- PASS --> Pipeline continues
   |
   +---- FAIL --> Pipeline fails
```

---

# 52. CI/CD Providers

MVP integrations:

```text
Jenkins
GitHub Actions
GitLab CI
Azure DevOps
```

Generic API/webhook support should be built first.

---

# 53. API Authentication

Support:

```text
JWT
API Keys
OAuth2/OIDC
```

MVP:

```text
JWT + API Keys
```

Enterprise:

```text
OIDC
SAML
LDAP
```

---

# 54. RBAC

Roles:

```text
Super Admin
Organization Admin
Project Admin
Performance Engineer
Tester
Viewer
CI/CD Service Account
```

Permissions:

```text
project.read
project.write
script.read
script.write
test.read
test.write
test.execute
test.stop
results.read
reports.create
admin.users
admin.system
```

---

# 55. Multi-Tenancy

Support:

```text
Organization
 |
 +-- Users
 +-- Projects
 +-- Resources
 +-- Tests
 +-- Load Generators
```

Every major entity contains:

```text
organization_id
```

Projects additionally contain:

```text
project_id
```

---

# 56. Database Schema

## organizations

```sql
CREATE TABLE organizations (
    id UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(100) UNIQUE NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
```

## users

```sql
CREATE TABLE users (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id),
    email VARCHAR(320) NOT NULL,
    name VARCHAR(255) NOT NULL,
    password_hash TEXT,
    status VARCHAR(50) NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    UNIQUE (organization_id, email)
);
```

## projects

```sql
CREATE TABLE projects (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id),
    name VARCHAR(255) NOT NULL,
    project_key VARCHAR(50) NOT NULL,
    description TEXT,
    status VARCHAR(50) NOT NULL DEFAULT 'ACTIVE',
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    UNIQUE (organization_id, project_key)
);
```

---

# 57. Scripts

```sql
CREATE TABLE scripts (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id),
    project_id UUID NOT NULL REFERENCES projects(id),
    name VARCHAR(255) NOT NULL,
    protocol VARCHAR(100) NOT NULL,
    description TEXT,
    status VARCHAR(50) NOT NULL DEFAULT 'ACTIVE',
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
```

---

# 58. Script Versions

```sql
CREATE TABLE script_versions (
    id UUID PRIMARY KEY,
    script_id UUID NOT NULL REFERENCES scripts(id),
    version INTEGER NOT NULL,
    storage_key TEXT NOT NULL,
    checksum VARCHAR(128),
    metadata JSONB,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE(script_id, version)
);
```

---

# 59. Performance Tests

```sql
CREATE TABLE performance_tests (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id),
    project_id UUID NOT NULL REFERENCES projects(id),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    status VARCHAR(50) NOT NULL DEFAULT 'DRAFT',
    configuration JSONB NOT NULL,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
```

---

# 60. Test Scripts

```sql
CREATE TABLE performance_test_scripts (
    id UUID PRIMARY KEY,
    performance_test_id UUID NOT NULL REFERENCES performance_tests(id),
    script_version_id UUID NOT NULL REFERENCES script_versions(id),
    virtual_users INTEGER NOT NULL DEFAULT 1,
    percentage NUMERIC(5,2),
    execution_order INTEGER NOT NULL
);
```

---

# 61. Load Generators

```sql
CREATE TABLE load_generators (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id),
    name VARCHAR(255) NOT NULL,
    hostname VARCHAR(255),
    ip_address INET,
    region VARCHAR(100),
    status VARCHAR(50) NOT NULL,
    agent_version VARCHAR(100),
    capacity INTEGER NOT NULL DEFAULT 1000,
    current_users INTEGER NOT NULL DEFAULT 0,
    last_heartbeat TIMESTAMPTZ,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
```

---

# 62. Test Runs

```sql
CREATE TABLE test_runs (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id),
    project_id UUID NOT NULL REFERENCES projects(id),
    performance_test_id UUID NOT NULL REFERENCES performance_tests(id),
    status VARCHAR(50) NOT NULL,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    initiated_by UUID REFERENCES users(id),
    configuration_snapshot JSONB NOT NULL,
    summary JSONB,
    created_at TIMESTAMPTZ NOT NULL
);
```

The `configuration_snapshot` is important.

A test run must never depend on mutable test configuration after execution begins.

---

# 63. Test Run Load Generators

```sql
CREATE TABLE test_run_load_generators (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES test_runs(id),
    load_generator_id UUID NOT NULL REFERENCES load_generators(id),
    assigned_users INTEGER NOT NULL,
    status VARCHAR(50) NOT NULL
);
```

---

# 64. Transactions

```sql
CREATE TABLE transactions (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES test_runs(id),
    name VARCHAR(255) NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ NOT NULL,
    duration_ms BIGINT NOT NULL,
    status VARCHAR(50) NOT NULL,
    error_code VARCHAR(100),
    error_message TEXT,
    load_generator_id UUID,
    virtual_user_id VARCHAR(255)
);
```

For very high volume, transaction-level raw events should be moved to a time-series/event store.

---

# 65. Time-Series Metrics

Use TimescaleDB hypertables.

Conceptual schema:

```sql
CREATE TABLE performance_metrics (
    time TIMESTAMPTZ NOT NULL,
    run_id UUID NOT NULL,
    metric_name VARCHAR(255) NOT NULL,
    entity_type VARCHAR(100),
    entity_id VARCHAR(255),
    value DOUBLE PRECISION NOT NULL,
    tags JSONB
);
```

Then:

```sql
SELECT create_hypertable(
    'performance_metrics',
    'time'
);
```

---

# 66. SLA Rules

```sql
CREATE TABLE sla_rules (
    id UUID PRIMARY KEY,
    performance_test_id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    metric VARCHAR(255) NOT NULL,
    operator VARCHAR(20) NOT NULL,
    threshold DOUBLE PRECISION NOT NULL,
    unit VARCHAR(50),
    severity VARCHAR(50) NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE
);
```

---

# 67. Baselines

```sql
CREATE TABLE baselines (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(id),
    run_id UUID NOT NULL REFERENCES test_runs(id),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL
);
```

---

# 68. Audit Logs

```sql
CREATE TABLE audit_logs (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL,
    user_id UUID,
    action VARCHAR(255) NOT NULL,
    entity_type VARCHAR(100),
    entity_id UUID,
    ip_address INET,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL
);
```

Audit everything important:

```text
Login
Logout
Project creation
Script upload
Script deletion
Test modification
Test execution
Test stop
User creation
Role change
API key creation
Configuration changes
```

---

# 69. Backend Module Structure

Recommended:

```text
backend/
├── app/
│   ├── main.py
│   ├── config.py
│   │
│   ├── api/
│   │   ├── auth.py
│   │   ├── users.py
│   │   ├── projects.py
│   │   ├── scripts.py
│   │   ├── tests.py
│   │   ├── runs.py
│   │   ├── load_generators.py
│   │   ├── results.py
│   │   ├── reports.py
│   │   ├── baselines.py
│   │   └── integrations.py
│   │
│   ├── domain/
│   │   ├── projects/
│   │   ├── scripts/
│   │   ├── tests/
│   │   ├── execution/
│   │   ├── metrics/
│   │   └── reporting/
│   │
│   ├── models/
│   ├── repositories/
│   ├── services/
│   ├── workers/
│   ├── events/
│   ├── security/
│   └── telemetry/
│
├── migrations/
├── tests/
└── pyproject.toml
```

---

# 70. Frontend Structure

```text
frontend/
├── app/
│   ├── login/
│   ├── dashboard/
│   ├── projects/
│   ├── scripts/
│   ├── tests/
│   ├── runs/
│   ├── results/
│   ├── reports/
│   ├── load-generators/
│   └── admin/
│
├── components/
│   ├── layout/
│   ├── navigation/
│   ├── charts/
│   ├── tables/
│   ├── forms/
│   ├── dialogs/
│   ├── tests/
│   ├── runs/
│   └── metrics/
│
├── lib/
│   ├── api.ts
│   ├── websocket.ts
│   ├── auth.ts
│   └── formatting.ts
│
├── hooks/
├── types/
└── styles/
```

---

# 71. REST API

Base:

```text
/api/v1
```

---

# 72. Authentication API

```http
POST /auth/login
POST /auth/logout
POST /auth/refresh
GET  /auth/me
```

---

# 73. Project API

```http
GET    /projects
POST   /projects
GET    /projects/{id}
PATCH  /projects/{id}
DELETE /projects/{id}
```

---

# 74. Script API

```http
GET    /projects/{projectId}/scripts
POST   /projects/{projectId}/scripts
GET    /scripts/{id}
PATCH  /scripts/{id}
DELETE /scripts/{id}

POST   /scripts/{id}/versions
GET    /scripts/{id}/versions
GET    /script-versions/{id}
```

---

# 75. Performance Test API

```http
GET    /projects/{projectId}/tests
POST   /projects/{projectId}/tests
GET    /tests/{id}
PATCH  /tests/{id}
DELETE /tests/{id}

POST   /tests/{id}/validate
POST   /tests/{id}/start
POST   /tests/{id}/schedule
POST   /tests/{id}/clone
```

---

# 76. Run API

```http
GET    /runs
GET    /runs/{id}
POST   /tests/{id}/runs
POST   /runs/{id}/stop
POST   /runs/{id}/cancel
GET    /runs/{id}/status
GET    /runs/{id}/metrics
GET    /runs/{id}/transactions
GET    /runs/{id}/errors
GET    /runs/{id}/logs
```

---

# 77. Load Generator API

```http
GET    /load-generators
POST   /load-generators
GET    /load-generators/{id}
PATCH  /load-generators/{id}
DELETE /load-generators/{id}

POST   /load-generators/{id}/enable
POST   /load-generators/{id}/disable
POST   /load-generators/{id}/test-connection
```

---

# 78. Report API

```http
POST /runs/{id}/reports
GET  /runs/{id}/reports
GET  /reports/{id}
GET  /reports/{id}/download
```

---

# 79. WebSocket API

Example:

```text
/ws/runs/{runId}
```

Events:

```json
{
  "type": "metric",
  "run_id": "123",
  "timestamp": "2026-08-10T10:00:00Z",
  "metric": {
    "active_users": 1250,
    "throughput": 840,
    "p95": 720,
    "error_rate": 0.4
  }
}
```

Other events:

```text
run.started
run.running
run.warning
run.failed
run.completed
transaction.metric
load-generator.status
sla.violation
```

---

# 80. Execution Orchestrator

Core service:

```text
ExecutionManager
```

Responsibilities:

```text
1. Validate test
2. Resolve script versions
3. Calculate VU allocation
4. Select load generators
5. Reserve resources
6. Create run
7. Create execution plan
8. Dispatch agents
9. Monitor heartbeats
10. Collect metrics
11. Detect failures
12. Stop execution
13. Release resources
14. Finalize results
15. Evaluate SLA
16. Generate summary
```

---

# 81. Execution Plan

Example:

```json
{
  "runId": "run-123",
  "duration": 1800,
  "rampUp": 600,
  "rampDown": 300,
  "scripts": [
    {
      "scriptVersion": "script-v4",
      "users": 1000
    }
  ],
  "loadGenerators": [
    {
      "id": "lg-01",
      "users": 500
    },
    {
      "id": "lg-02",
      "users": 500
    }
  ]
}
```

The execution plan is immutable.

---

# 82. Resource Allocation

Use distributed locking.

Example:

```text
Redis lock:

load-generator:{id}:allocation
```

Before allocating:

```text
available_capacity >= requested_users
```

Reserve:

```text
capacity -= requested
```

Release after test.

---

# 83. Concurrency

Multiple tests must be supported.

Example:

```text
Test A -> LG01 + LG02
Test B -> LG03
Test C -> LG04 + LG05
```

The allocator must prevent over-allocation.

---

# 84. Failure Handling

If a load generator dies:

```text
LG failure
    |
    v
Heartbeat timeout
    |
    v
Mark OFFLINE
    |
    v
Determine impact
    |
    +--> recoverable
    |
    +--> unrecoverable
```

Possible strategies:

```text
Continue with reduced load
Stop test
Rebalance users
Fail test
```

MVP default:

```text
Fail test if capacity loss exceeds configurable percentage.
```

---

# 85. Idempotency

All execution commands must be idempotent.

Example:

```text
POST /runs/123/stop
```

If already stopped:

```text
200 OK
```

rather than executing the stop twice.

---

# 86. Security Architecture

All communication:

```text
TLS 1.2+
```

Passwords:

```text
Argon2id
```

Secrets:

```text
Never store plaintext credentials.
```

Use:

```text
Vault
AWS Secrets Manager
Azure Key Vault
Kubernetes Secrets
```

depending on deployment.

---

# 87. API Security

Implement:

```text
Rate limiting
Request validation
RBAC
Tenant isolation
CSRF protection where applicable
CORS
Security headers
JWT expiration
Refresh token rotation
API-key hashing
Audit logging
```

---

# 88. API Keys

Never store:

```text
raw API key
```

Store:

```text
SHA-256/HMAC hash
```

Display only once.

Example:

```text
lre_live_************************
```

---

# 89. Secrets Used by Scripts

Do not put passwords directly into scripts.

Support variables:

```text
${USERNAME}
${PASSWORD}
${BASE_URL}
```

Runtime environment:

```text
Test
 |
 v
Secret Resolver
 |
 v
Load Generator
```

---

# 90. Environment Configuration

Support:

```text
DEV
TEST
STAGING
PRODUCTION
```

Environment variables:

```text
DATABASE_URL
REDIS_URL
RABBITMQ_URL
S3_ENDPOINT
S3_BUCKET
JWT_SECRET
OTEL_ENDPOINT
```

---

# 91. Docker Development

Services:

```text
frontend
backend
worker
scheduler
load-generator
postgres
timescaledb
redis
rabbitmq
minio
grafana
prometheus
```

A local environment should be started with:

```bash
docker compose up -d
```

---

# 92. Docker Compose

Conceptual:

```yaml
services:

  frontend:
    build: ./frontend
    ports:
      - "3000:3000"

  api:
    build: ./backend
    ports:
      - "8000:8000"

  worker:
    build: ./backend

  scheduler:
    build: ./backend

  postgres:
    image: postgres:16

  redis:
    image: redis:7

  rabbitmq:
    image: rabbitmq:3-management

  minio:
    image: minio/minio

  prometheus:
    image: prom/prometheus

  grafana:
    image: grafana/grafana
```

---

# 93. Kubernetes Architecture

Production deployment:

```text
                         Internet
                            |
                       Ingress
                            |
                     API / Frontend
                            |
                 +----------+----------+
                 |                     |
             API Pods             Worker Pods
                 |                     |
                 +----------+----------+
                            |
                       RabbitMQ
                            |
            +---------------+---------------+
            |               |               |
       Execution        Metrics         Scheduler
         Pods            Pods             Pod
            |               |
            v               v
       Load Generators   TimescaleDB
                            |
                        PostgreSQL
                            |
                         S3/Blob
```

---

# 94. Kubernetes Namespaces

```text
lre-platform
lre-execution
lre-monitoring
```

---

# 95. Observability

Every component must expose:

```text
/health
/ready
/metrics
```

Metrics:

```text
API requests
API latency
queue depth
job failures
execution count
active tests
load-generator health
metric ingestion rate
database latency
```

---

# 96. Logging

Use structured JSON logs.

Example:

```json
{
  "timestamp": "2026-08-10T10:10:00Z",
  "level": "INFO",
  "service": "execution-manager",
  "run_id": "run-123",
  "event": "load_generator_allocated",
  "load_generator_id": "lg-01"
}
```

---

# 97. Distributed Tracing

Use:

```text
OpenTelemetry
```

Trace:

```text
POST /runs
 |
 +-- database
 |
 +-- execution planner
 |
 +-- RabbitMQ
 |
 +-- load generator
 |
 +-- metrics collector
```

---

# 98. Monitoring Stack

Recommended:

```text
Prometheus
Grafana
OpenTelemetry
Loki
Tempo
```

Architecture:

```text
Application
   |
   +--> Prometheus
   +--> Loki
   +--> Tempo
            |
            v
          Grafana
```

---

# 99. Frontend Components

## Core

```text
AppShell
Sidebar
Topbar
ProjectSelector
UserMenu
Breadcrumbs
PageHeader
```

## Data

```text
DataTable
MetricCard
StatusBadge
EmptyState
LoadingState
ErrorState
Pagination
FilterBar
SearchBox
```

## Testing

```text
TestDesigner
ScriptSelector
WorkloadEditor
LoadGeneratorSelector
ScheduleEditor
ThresholdEditor
RunControls
```

## Analytics

```text
MetricChart
PercentileChart
ThroughputChart
ErrorChart
VUChart
ComparisonChart
```

---

# 100. Test Designer UI

Recommended layout:

```text
+-----------------------------------------------------+
| Checkout Peak Load                      [Save] [Run] |
+-----------------------------------------------------+
| 1 General | 2 Scripts | 3 Workload | 4 Resources   |
| 5 Schedule | 6 SLA | 7 Review                      |
+-----------------------------------------------------+
|                                                     |
|                    Form                             |
|                                                     |
+-----------------------------------------------------+
| Back                                      Continue  |
+-----------------------------------------------------+
```

---

# 101. Workload Editor UI

```text
Workload

Total Users: [ 5000 ]

Script                 Users      %
------------------------------------------------
Login                  500        10%
Browse                 2500       50%
Add Cart               1250       25%
Checkout               750        15%

                              Total: 100%
```

Add:

```text
+ Add Script
```

---

# 102. Load Generator Selector

```text
Load Generators

Name       Region       Capacity   Available   Status

LG-01      EU           5000       4000        ONLINE
LG-02      EU           5000       5000        ONLINE
LG-03      US           3000       2800        ONLINE

[Auto Allocate]
[Manual Allocation]
```

---

# 103. Live Run UI

```text
RUNNING

Active Users          4,812
Throughput            3,840 req/s
Avg Response          420 ms
P95                   910 ms
Error Rate            0.31%

----------------------------------------------------

Response Time
             /\/\____/\/\____
        ____/              \____

----------------------------------------------------

Throughput
       _________
 ____/         \________

----------------------------------------------------

Transactions

Login       99.9% PASS
Browse      99.7% PASS
Checkout    98.8% PASS
```

---

# 104. Results UI

After completion:

```text
PASSED

Duration: 30m
Users: 5,000
Requests: 6,840,123

P95: 842ms
P99: 1,420ms
Errors: 0.21%

SLA:
12/12 PASS
```

---

# 105. Error Analysis

Display:

```text
Error Code
Message
Count
First Seen
Last Seen
Affected Transaction
Affected Load Generator
```

Example:

```text
HTTP 500
Checkout service unavailable

Count: 12,402
```

---

# 106. Resource Monitoring

During test:

```text
Load Generator CPU
Load Generator Memory
Network
Disk
```

Application metrics:

```text
Application CPU
Application memory
Database connections
Database CPU
HTTP server requests
```

---

# 107. Database Monitoring

Optional integrations:

```text
PostgreSQL
MySQL
Oracle
SQL Server
```

Metrics:

```text
CPU
Connections
Locks
Queries/sec
Latency
Cache hit ratio
```

---

# 108. Notification System

Events:

```text
Test Started
Test Completed
Test Failed
SLA Violated
Load Generator Offline
Report Ready
```

Channels:

```text
Email
Slack
Microsoft Teams
Webhook
```

---

# 109. Notification Model

```sql
CREATE TABLE notifications (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL,
    user_id UUID,
    type VARCHAR(100),
    title VARCHAR(255),
    message TEXT,
    read_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL
);
```

---

# 110. Auditability

Every test execution should have a traceable chain:

```text
User
 |
 v
Test Configuration
 |
 v
Test Version
 |
 v
Run
 |
 v
Load Generator
 |
 v
Metrics
 |
 v
Result
 |
 v
Report
```

---

# 111. Versioning

Version:

```text
Scripts
Performance Tests
Workloads
Thresholds
Reports
Configuration
```

Never modify historical execution configurations.

---

# 112. Data Retention

Recommended defaults:

```text
Raw request metrics: 7 days
Aggregated metrics: 180 days
Test summaries: 2 years
Reports: 2 years
Audit logs: 1 year
```

Configurable by organization.

---

# 113. Data Lifecycle

```text
Raw
 |
 v
Short-term storage
 |
 v
Aggregation
 |
 v
Long-term metrics
 |
 v
Retention policy
 |
 v
Deletion/archive
```

---

# 114. Search

Global search should support:

```text
Projects
Scripts
Tests
Runs
Reports
Load Generators
Users
```

Use PostgreSQL full-text search initially.

Later:

```text
OpenSearch
Elasticsearch
```

---

# 115. Filtering

Every major table should support:

```text
Search
Status
Date
Project
Owner
Environment
Tags
```

---

# 116. Tags

Entities should support:

```text
tags
```

Example:

```text
production
nightly
smoke
regression
release-2.3
critical
```

---

# 117. Requirements

Optional MVP feature:

```text
Requirement
 |
 +-- Performance objective
 +-- SLA
 +-- Test
 +-- Result
```

Example:

```text
REQ-1024

Checkout must support:
5000 concurrent users
with P95 < 2 seconds.
```

---

# 118. Test Templates

Support reusable templates:

```text
Load Test
Stress Test
Spike Test
Soak Test
Capacity Test
Smoke Performance Test
```

---

# 119. Test Types

## Load Test

Normal expected load.

## Stress Test

Increasing load until degradation.

## Spike Test

Sudden load increase.

## Soak Test

Long-duration execution.

## Capacity Test

Determine maximum sustainable load.

---

# 120. MVP Test Lifecycle

```text
Draft
 |
 v
Validated
 |
 v
Approved
 |
 v
Scheduled
 |
 v
Running
 |
 v
Analyzed
 |
 v
Archived
```

---

# 121. Validation

Before running:

```text
Test has script
Test has users
Workload sums to 100%
Load generators available
Script versions valid
Thresholds valid
Environment configured
Credentials available
```

If invalid:

```text
RUN DISABLED
```

with actionable errors.

---

# 122. Execution Preflight

Example:

```text
✓ Scripts available
✓ Load generators online
✓ 5000 VUs capacity available
✓ Environment reachable
✓ Credentials available
✓ SLA rules valid

Ready to run.
```

---

# 123. Environment Connectivity Test

Before execution:

```text
DNS
TCP
TLS
HTTP
Authentication
```

Test:

```text
GET /health
```

---

# 124. Script Validation

Validate:

```text
Syntax
Required variables
Referenced files
Environment variables
Executor compatibility
```

---

# 125. Variables

Support:

```text
Static
Environment
CSV
Random
Generated
Secret
```

Example:

```text
BASE_URL
USERNAME
PASSWORD
PRODUCT_ID
```

---

# 126. Parameterization

Example CSV:

```csv
username,password
user001,password1
user002,password2
user003,password3
```

Runtime behavior:

```text
VU1 -> user001
VU2 -> user002
VU3 -> user003
```

---

# 127. Correlation

For HTTP executor, provide a mechanism to capture dynamic values.

Example:

```text
Response:
{
  "token": "abc123"
}
```

Extract:

```text
$.token
```

Use:

```text
${token}
```

---

# 128. HTTP Executor

Minimum capabilities:

```text
GET
POST
PUT
PATCH
DELETE
HEAD
OPTIONS
```

Support:

```text
Headers
Query parameters
Cookies
JSON
Form data
Multipart
TLS
Authentication
Redirects
Timeouts
Retries
```

---

# 129. HTTP Assertions

Support:

```text
Status code
Response time
Body contains
JSONPath
Regex
Header
```

Example:

```text
Expected status:
200

JSONPath:
$.status == "success"
```

---

# 130. Transactions

Scripts should support logical transactions:

```text
startTransaction("Login")

request()

endTransaction("Login")
```

Metrics are aggregated by transaction name.

---

# 131. Think Time

Support:

```text
Fixed
Random
Range
```

Example:

```text
2-5 seconds
```

---

# 132. Pacing

Support:

```text
Start next iteration every:
10 seconds
```

---

# 133. Ramp-up

Example:

```text
0 users
   |
   | 10 minutes
   v
5000 users
```

Supported profiles:

```text
Linear
Step
Custom
```

---

# 134. Ramp-down

```text
5000
 |
 | 5 minutes
 v
0
```

---

# 135. Executor Container

Each executor can run inside an isolated container.

```text
executor/
├── Dockerfile
├── runner
├── protocol
├── metrics
└── entrypoint
```

This prevents executor failures from taking down the controller.

---

# 136. Load Generator Container

Conceptually:

```text
+--------------------------------------+
| Load Generator Agent                 |
|                                      |
| +--------------------------------+   |
| | Executor Runtime               |   |
| |                                |   |
| | VU1 VU2 VU3 ... VUn            |   |
| +--------------------------------+   |
|                                      |
| Metrics Collector                    |
| Heartbeat                            |
| Resource Monitor                     |
+--------------------------------------+
```

---

# 137. Container Isolation

Each test can receive:

```text
CPU limits
Memory limits
Network limits
File-system limits
```

This allows multiple tests per physical host.

---

# 138. Capacity Model

Each load generator advertises:

```text
max_virtual_users
cpu_cores
memory_mb
network_mbps
supported_executors
```

Example:

```json
{
  "maxVirtualUsers": 5000,
  "cpuCores": 8,
  "memoryMb": 16384,
  "supportedExecutors": [
    "http",
    "k6"
  ]
}
```

---

# 139. Scheduling Algorithm

Initial algorithm:

```text
1. Filter ONLINE generators
2. Filter supported executor
3. Filter region/environment
4. Sort by available capacity
5. Allocate users
6. Reserve resources
```

Later:

```text
bin packing
weighted allocation
geographic routing
cost optimization
```

---

# 140. Run Cancellation

Cancellation flow:

```text
User presses STOP
       |
       v
Controller
       |
       v
Set STOPPING
       |
       v
Send stop command
       |
       v
Agents terminate VUs
       |
       v
Upload final metrics
       |
       v
Release capacity
       |
       v
Set CANCELLED
```

---

# 141. Graceful Shutdown

Services must handle:

```text
SIGTERM
```

Workers:

```text
Stop accepting new jobs
Finish current safe operation
Persist state
Disconnect
Exit
```

---

# 142. Disaster Recovery

PostgreSQL:

```text
Daily backup
Point-in-time recovery
```

Object storage:

```text
Versioning
Replication
```

RabbitMQ:

```text
Durable queues
Persistent messages
```

---

# 143. High Availability

Production:

```text
2+ API replicas
2+ worker replicas
2+ scheduler replicas with leader election
3-node PostgreSQL or managed PostgreSQL
3-node RabbitMQ where appropriate
Redis HA
```

---

# 144. Scaling

API:

```text
Horizontal
```

Workers:

```text
Horizontal
```

Metrics:

```text
Horizontal
```

Load generators:

```text
Horizontal
```

Database:

```text
Vertical first
Read replicas later
```

---

# 145. Performance Targets

MVP control plane targets:

```text
API p95 < 500ms
Dashboard initial load < 2 seconds
WebSocket metric latency < 2 seconds
Test start command < 5 seconds
```

These targets apply to platform operations, not the application under test.

---

# 146. Scale Targets

Initial MVP:

```text
100 concurrent users of platform
20 projects
1000 scripts
100 active tests
50 load generators
50,000 virtual users
```

Stretch:

```text
500 platform users
500 load generators
1,000,000 virtual users
```

Actual load-generation capacity depends on executor complexity and hardware.

---

# 147. Testing Strategy

## Unit Tests

Backend:

```text
pytest
```

Frontend:

```text
Vitest
```

---

# 148. Integration Tests

Test:

```text
API + PostgreSQL
API + Redis
API + RabbitMQ
API + Object Storage
Executor + Agent
Agent + Controller
Metrics pipeline
```

---

# 149. End-to-End Tests

Use:

```text
Playwright
```

Scenario:

```text
Login
 |
Create Project
 |
Upload Script
 |
Create Test
 |
Register LG
 |
Run Test
 |
View Metrics
 |
Complete Test
 |
Generate Report
 |
Compare Baseline
```

---

# 150. Load Testing the Platform

The platform itself must be load tested.

Test:

```text
100 concurrent users
1000 API requests/sec
100 active test runs
1 million metric events/minute
```

---

# 151. CI Pipeline

```text
Pull Request
     |
     v
Lint
     |
     v
Type Check
     |
     v
Unit Tests
     |
     v
Integration Tests
     |
     v
Security Scan
     |
     v
Build Docker Images
     |
     v
E2E Tests
     |
     v
Publish
```

---

# 152. Security Scanning

Use:

```text
Trivy
Semgrep
OWASP dependency checks
npm audit
pip-audit
```

---

# 153. Database Migrations

Use:

```text
Alembic
```

Migration process:

```text
Developer
 |
 v
alembic revision
 |
 v
Review
 |
 v
CI migration test
 |
 v
Deployment migration
```

Never manually modify production schema.

---

# 154. API Documentation

FastAPI automatically provides:

```text
OpenAPI
Swagger UI
ReDoc
```

Expose:

```text
/api/docs
/api/openapi.json
```

---

# 155. API Versioning

Use:

```text
/api/v1
```

Future:

```text
/api/v2
```

Do not break v1 contracts without a migration strategy.

---

# 156. Error Response Format

Standardize:

```json
{
  "error": {
    "code": "TEST_NOT_RUNNABLE",
    "message": "No load generator has sufficient capacity.",
    "details": {
      "requiredUsers": 5000,
      "availableUsers": 3200
    },
    "requestId": "req-123"
  }
}
```

---

# 157. Frontend Error Handling

Display:

```text
What happened
Why it happened
What the user can do
```

Bad:

```text
500 Internal Server Error
```

Good:

```text
Test cannot start because the selected load generators
have only 3,200 available virtual-user capacity while
the test requires 5,000.

[View Load Generators]
```

---

# 158. Empty States

Example:

```text
No performance tests yet.

Create your first performance test to start measuring
application behavior under load.

[Create Performance Test]
```

---

# 159. Permissions in UI

Do not merely hide buttons.

Backend must enforce authorization.

Frontend:

```text
if can("test.execute"):
    show Run
```

Backend:

```text
authorize(user, "test.execute")
```

---

# 160. Project-Level Permissions

Example:

```text
Project A
  Alice -> Admin
  Bob   -> Engineer
  Carol -> Viewer
```

---

# 161. Organization-Level Permissions

Example:

```text
Organization
 |
 +-- Admins
 +-- Engineers
 +-- Viewers
```

Project permissions override organization defaults only according to an explicit permission model.

---

# 162. Data Isolation

Every query must be scoped by:

```text
organization_id
```

and where appropriate:

```text
project_id
```

Never trust IDs supplied by clients.

---

# 163. SQL Query Security

Use:

```text
SQLAlchemy parameterization
```

Never construct:

```python
f"SELECT ... WHERE id = '{id}'"
```

---

# 164. Frontend State Management

Use:

```text
TanStack Query
```

for server state.

Use React state/context only for:

```text
UI state
dialogs
filters
temporary form state
```

Avoid a massive global Redux store.

---

# 165. Real-Time State

Use:

```text
WebSocket
+
TanStack Query cache invalidation
```

Example:

```text
run.completed
       |
       v
invalidate:
runs/{id}
results/{id}
dashboard
```

---

# 166. Charts

Use ECharts for:

* large time-series datasets
* zoom
* brush selection
* multiple series
* synchronized charts

Use Recharts for simple cards/charts.

---

# 167. Table Requirements

Tables should support:

```text
Sorting
Filtering
Pagination
Column visibility
CSV export
Row actions
Bulk actions
```

---

# 168. Accessibility

Target:

```text
WCAG 2.1 AA
```

Requirements:

```text
Keyboard navigation
ARIA labels
Focus management
Color-independent status
Screen-reader-compatible forms
Accessible charts where practical
```

---

# 169. Internationalization

Architecture should support:

```text
en
fr
de
es
pt
```

MVP can ship English only.

Never hard-code user-facing strings deep inside business logic.

---

# 170. Time Zones

Store timestamps as:

```text
UTC
```

Display in:

```text
user/project timezone
```

---

# 171. Date Handling

Backend:

```text
UTC
ISO-8601
```

Frontend:

```text
localized display
```

---

# 172. Feature Flags

Use feature flags for:

```text
new dashboard
k6 executor
advanced analytics
AI assistant
cloud load generators
```

Example:

```text
FEATURE_K6_EXECUTOR=true
```

---

# 173. Plugin Architecture

Executors should be plugins.

```text
Executor Registry
 |
 +-- HTTP
 +-- k6
 +-- JMeter
 +-- Gatling
 +-- Custom
```

Monitors should also be plugins.

```text
Monitor Registry
 |
 +-- Prometheus
 +-- OpenTelemetry
 +-- Azure Monitor
 +-- CloudWatch
 +-- Dynatrace
```

---

# 174. Integration Architecture

Generic connector interface:

```typescript
interface Integration {
  validate(config): Promise<Result>;
  connect(): Promise<void>;
  disconnect(): Promise<void>;
  collect(): Promise<Metric[]>;
}
```

---

# 175. Webhook Integration

Events:

```text
test.started
test.completed
test.failed
sla.failed
```

Payload:

```json
{
  "event": "test.completed",
  "testId": "123",
  "runId": "456",
  "status": "PASSED",
  "timestamp": "2026-08-10T10:30:00Z"
}
```

---

# 176. CI API Contract

Example:

```http
POST /api/v1/tests/123/runs
Authorization: Bearer ...
Idempotency-Key: build-123
```

Response:

```json
{
  "runId": "run-456",
  "status": "QUEUED"
}
```

CI can poll:

```http
GET /api/v1/runs/run-456
```

---

# 177. CLI

A CLI should eventually be provided.

Example:

```bash
lre login
lre project list
lre test list
lre test run checkout
lre run status RUN-123
lre run stop RUN-123
lre report download RUN-123
```

---

# 178. MVP CLI

Minimum:

```bash
lre login
lre test run <test>
lre run status <run>
lre run wait <run>
```

---

# 179. Example Project Structure

```text
loadrunner-enterprise-mvp/
│
├── README.md
├── LICENSE
├── docker-compose.yml
├── .env.example
├── Makefile
│
├── frontend/
│
├── backend/
│
├── agent/
│
├── executors/
│   ├── http/
│   ├── k6/
│   └── jmeter/
│
├── worker/
│
├── scheduler/
│
├── infrastructure/
│   ├── docker/
│   ├── kubernetes/
│   ├── terraform/
│   └── monitoring/
│
├── scripts/
│
├── docs/
│
└── tests/
```

---

# 180. Recommended MVP Repository

More specifically:

```text
lre-mvp/
├── apps/
│   ├── web/
│   ├── api/
│   ├── agent/
│   └── worker/
│
├── packages/
│   ├── contracts/
│   ├── executor-sdk/
│   ├── ui/
│   └── config/
│
├── infrastructure/
│   ├── docker/
│   ├── kubernetes/
│   └── terraform/
│
├── docs/
├── tests/
└── docker-compose.yml
```

---

# 181. Shared Contracts

Create common schemas:

```text
Run
Test
Script
Metric
Transaction
LoadGenerator
SLA
Report
```

Use:

```text
OpenAPI
JSON Schema
TypeScript types
Pydantic models
```

Generate client types where possible.

---

# 182. Development Workflow

```text
Developer
   |
   v
Create branch
   |
   v
Implement
   |
   v
Unit tests
   |
   v
Integration tests
   |
   v
Pull Request
   |
   v
CI
   |
   v
Review
   |
   v
Merge
   |
   v
Build image
   |
   v
Deploy
```

---

# 183. Local Developer Experience

One command:

```bash
make dev
```

Should:

```text
Start infrastructure
Run migrations
Seed admin
Start backend
Start frontend
Start worker
Start scheduler
```

---

# 184. Seed Data

Create:

```text
Organization:
Demo Organization

Admin:
admin@example.com

Project:
Demo E-Commerce

Load Generators:
LG-Demo-01
LG-Demo-02

Scripts:
Login
Browse Products
Checkout

Test:
Demo Load Test
```

---

# 185. Demo Test

Default test:

```text
Name:
Demo E-Commerce Load Test

Users:
100

Duration:
5 minutes

Ramp-up:
1 minute

Scripts:
Login       20%
Browse      50%
Checkout    30%
```

---

# 186. Demo Dashboard

After starting the demo:

```text
Active Users
Throughput
Response Time
P95
Errors
Transactions
```

The platform should visibly demonstrate the complete workflow.

---

# 187. MVP Milestones

## Phase 0 — Foundation

Duration:

```text
1-2 weeks
```

Build:

```text
Repository
Docker
PostgreSQL
Redis
RabbitMQ
Backend skeleton
Frontend skeleton
Authentication
```

---

# 188. Phase 1 — Core Management

Build:

```text
Organizations
Users
RBAC
Projects
Scripts
Script versions
Test definitions
```

---

# 189. Phase 2 — Load Generation

Build:

```text
Agent
Agent registration
Heartbeats
HTTP executor
Execution orchestration
VU management
```

---

# 190. Phase 3 — Runtime

Build:

```text
Test start
Test stop
Test cancellation
WebSockets
Real-time metrics
Run state
Load generator monitoring
```

---

# 191. Phase 4 — Analytics

Build:

```text
Transactions
TimescaleDB
Charts
Errors
Percentiles
SLA
Baseline
Comparison
```

---

# 192. Phase 5 — Reporting

Build:

```text
HTML report
PDF report
CSV export
Report storage
Report history
```

---

# 193. Phase 6 — CI/CD

Build:

```text
API keys
CI endpoint
Webhook
Jenkins example
GitHub Actions example
CLI
```

---

# 194. Phase 7 — Enterprise Hardening

Build:

```text
OIDC
Audit
Secrets
Rate limiting
HA
Backups
Observability
Security scanning
```

---

# 195. MVP Definition of Done

The MVP is considered complete when a user can:

```text
[✓] Log in
[✓] Create organization
[✓] Create project
[✓] Create/upload script
[✓] Version script
[✓] Register load generator
[✓] See LG heartbeat
[✓] Create performance test
[✓] Assign script
[✓] Configure VUs
[✓] Configure ramp-up
[✓] Configure duration
[✓] Select LGs
[✓] Configure SLA
[✓] Validate test
[✓] Start test
[✓] Observe live metrics
[✓] Stop test
[✓] Complete test
[✓] View transactions
[✓] View errors
[✓] View percentiles
[✓] Evaluate SLA
[✓] Save baseline
[✓] Compare runs
[✓] Generate report
[✓] Download report
[✓] Trigger test via API
[✓] View audit history
```

---

# 196. Example End-to-End Scenario

## Step 1

User logs in.

```text
POST /auth/login
```

## Step 2

Creates:

```text
Project: Payments
```

## Step 3

Uploads:

```text
payment-api.js
```

## Step 4

Creates:

```text
Payment Peak Test
```

## Step 5

Configures:

```text
Users: 10,000
Ramp-up: 15 min
Duration: 30 min
Ramp-down: 5 min
```

## Step 6

Selects:

```text
LG-01
LG-02
LG-03
LG-04
```

## Step 7

Starts test.

```text
POST /tests/{id}/runs
```

## Step 8

Execution Manager allocates:

```text
LG-01 -> 2500
LG-02 -> 2500
LG-03 -> 2500
LG-04 -> 2500
```

## Step 9

Agents start.

## Step 10

Metrics stream to:

```text
RabbitMQ
   |
   v
Metrics Worker
   |
   v
TimescaleDB
```

## Step 11

WebSocket broadcasts:

```text
P95
TPS
VU
Errors
```

## Step 12

Test completes.

## Step 13

SLA engine evaluates:

```text
P95 < 2s
Error rate < 1%
TPS > 1000
```

## Step 14

Result:

```text
PASSED
```

## Step 15

User creates baseline.

## Step 16

Next release runs the same test.

## Step 17

Comparison:

```text
P95 +18%
Error rate +200%
TPS -4%
```

## Step 18

Platform identifies:

```text
PERFORMANCE REGRESSION
```

---

# 197. Architecture Decision Records

Create ADRs for:

```text
ADR-001 Modular monolith
ADR-002 PostgreSQL
ADR-003 TimescaleDB
ADR-004 RabbitMQ
ADR-005 Redis
ADR-006 S3-compatible storage
ADR-007 WebSocket runtime streaming
ADR-008 Executor plugin architecture
ADR-009 OpenTelemetry
ADR-010 React/Next.js
```

---

# 198. Important Architectural Principle

Do not make the execution engine part of the API process.

Bad:

```text
FastAPI
 |
 +-- starts 10,000 VUs
```

Good:

```text
FastAPI
 |
 v
Execution Queue
 |
 v
Execution Worker
 |
 v
Load Generator
```

This prevents a long-running test from blocking the control plane.

---

# 199. Second Important Principle

Do not use PostgreSQL as the high-volume metric event bus.

Bad:

```text
10,000 VUs
   |
   v
PostgreSQL INSERT
```

Good:

```text
VUs
 |
 v
Agent aggregation
 |
 v
Message Broker
 |
 v
Metrics Worker
 |
 v
TimescaleDB
```

---

# 200. Third Important Principle

Every test run gets an immutable configuration snapshot.

```text
Test v7
   |
   v
Run 123
   |
   +-- configuration snapshot
   +-- script versions
   +-- workload
   +-- LG allocation
   +-- SLA
```

This makes historical results reproducible.

---

# 201. Fourth Important Principle

Separate:

```text
Control Plane
```

from:

```text
Execution Plane
```

This is the most important architectural decision in the platform.

---

# 202. Fifth Important Principle

Build the executor abstraction before building multiple protocols.

```text
Executor Interface
       |
       +-- HTTP
       +-- k6
       +-- JMeter
       +-- Gatling
       +-- Future Protocol
```

Do not hard-code HTTP logic throughout the application.

---

# 203. Sixth Important Principle

Metrics should have a common schema.

```text
metric_name
timestamp
run_id
entity_type
entity_id
value
tags
```

This allows the UI to remain independent of the executor.

---

# 204. Seventh Important Principle

Treat the load generator as an untrusted remote worker.

The controller should assume:

```text
Agent can disappear
Agent can restart
Network can fail
Messages can duplicate
Metrics can arrive late
```

Therefore:

```text
Heartbeats
Idempotency
Retries
Timeouts
State reconciliation
```

are mandatory.

---

# 205. Eighth Important Principle

Never couple frontend screens directly to database models.

Use:

```text
Database
   |
Repository
   |
Domain Service
   |
API DTO
   |
Frontend
```

---

# 206. Ninth Important Principle

Use asynchronous jobs for:

```text
Reports
Large imports
Script processing
Test execution
Metrics aggregation
Notifications
```

---

# 207. Tenth Important Principle

The platform should be API-first.

Everything possible through the UI should eventually be possible through the API.

---

# 208. Recommended Initial Technology Matrix

| Layer          | Technology                    |
| -------------- | ----------------------------- |
| Frontend       | React + TypeScript + Next.js  |
| UI             | Tailwind + shadcn/ui          |
| State          | TanStack Query                |
| Charts         | ECharts                       |
| Editor         | Monaco                        |
| Backend        | FastAPI                       |
| ORM            | SQLAlchemy                    |
| Validation     | Pydantic                      |
| Database       | PostgreSQL                    |
| Time Series    | TimescaleDB                   |
| Cache          | Redis                         |
| Broker         | RabbitMQ                      |
| Storage        | S3 / MinIO                    |
| Workers        | Celery/Dramatiq               |
| Agent          | Python                        |
| Executor       | Python/Node containers        |
| Auth           | JWT + OIDC-ready              |
| Observability  | OpenTelemetry                 |
| Metrics        | Prometheus                    |
| Dashboards     | Grafana                       |
| Logs           | Loki                          |
| Traces         | Tempo                         |
| Containers     | Docker                        |
| Orchestration  | Kubernetes                    |
| IaC            | Terraform                     |
| E2E            | Playwright                    |
| Backend tests  | Pytest                        |
| Frontend tests | Vitest                        |
| CI             | GitHub Actions/GitLab/Jenkins |

---

# 209. Recommended MVP Scope

The first production-capable release should implement:

```text
              ┌─────────────────────────┐
              │        FRONTEND         │
              │                         │
              │ Dashboard               │
              │ Projects                │
              │ Scripts                 │
              │ Test Designer           │
              │ Run Dashboard           │
              │ Results                 │
              │ Reports                 │
              │ Administration          │
              └───────────┬─────────────┘
                          │
                          v
              ┌─────────────────────────┐
              │        REST API         │
              └───────────┬─────────────┘
                          │
         ┌────────────────┼────────────────┐
         │                │                │
         v                v                v
    PostgreSQL          Redis          RabbitMQ
         │                                 │
         │                                 │
         │                         ┌───────┴────────┐
         │                         │                │
         v                         v                v
    Metadata                  Scheduler       Executor
                                                  |
                                                  v
                                           Load Generator
                                                  |
                                                  v
                                           Target System
                                                  |
                                                  v
                                           Metrics Worker
                                                  |
                                                  v
                                            TimescaleDB
                                                  |
                                                  v
                                             Analytics
```

---

# 210. Future Enterprise Roadmap

After MVP:

## R2

```text
k6 executor
JMeter executor
Gatling executor
Advanced parameterization
Advanced correlation
Prometheus integration
Grafana integration
```

## R3

```text
Cloud load generators
AWS integration
Azure integration
GCP integration
Global regions
Dynamic scaling
```

## R4

```text
Service virtualization
Network virtualization
Advanced topology
Distributed tracing
```

## R5

```text
AI-assisted scripting
AI result analysis
Automatic bottleneck detection
Automatic baseline analysis
Natural-language test creation
```

---

# 211. AI Features — Future

An AI assistant could eventually accept:

```text
"Create a 5,000-user checkout load test
for 30 minutes with a 10-minute ramp-up."
```

and generate:

```text
Test
Workload
Scripts
Thresholds
Load-generator allocation
Schedule
```

It could also analyze:

```text
"P95 increased by 21%.
What changed?"
```

and correlate:

```text
Transaction
+
Infrastructure
+
Trace
+
Deployment
```

AI must remain an assistant; deterministic test execution and SLA evaluation should not depend on an LLM.

---

# 212. Reference Architecture Summary

The final architecture is:

```text
                         USERS
                           |
                           v
                    ┌──────────────┐
                    │ Web Frontend │
                    │ React/Next   │
                    └──────┬───────┘
                           |
                           v
                    ┌──────────────┐
                    │ API Gateway  │
                    │ FastAPI      │
                    └──────┬───────┘
                           |
             ┌─────────────┼─────────────┐
             |             |             |
             v             v             v
        PostgreSQL       Redis       RabbitMQ
             |                           |
             |                ┌──────────┼──────────┐
             |                |          |          |
             |                v          v          v
             |           Scheduler   Executor   Metrics
             |                          |         Worker
             |                          |            |
             |                          v            v
             |                    Load Generators  TimescaleDB
             |                          |            |
             |                          v            |
             |                    Target System      |
             |                                       |
             +-------------------+-------------------+
                                 |
                                 v
                         Analytics / Reports
                                 |
                                 v
                             Frontend
```

---

# 213. Final MVP Architecture Decision

The recommended implementation is **not** a collection of unrelated microservices.

Start with:

```text
1 React application
1 FastAPI application
1 Worker application
1 Load Generator Agent
1 HTTP executor
1 PostgreSQL/TimescaleDB
1 Redis
1 RabbitMQ
1 MinIO
1 OpenTelemetry/Prometheus stack
```

This gives the project enough architectural separation to scale while keeping the initial codebase manageable.

Once the platform reaches meaningful execution volume, individual components can be extracted into services without redesigning the domain model.

---

# 214. Final Build Order

Implement in exactly this order:

```text
01. Repository / monorepo
02. Docker development environment
03. PostgreSQL
04. Redis
05. RabbitMQ
06. MinIO
07. FastAPI skeleton
08. Authentication
09. RBAC
10. Organization
11. Project management
12. Script repository
13. Script versions
14. HTTP executor
15. Load-generator agent
16. Agent registration
17. Heartbeats
18. Performance-test designer
19. Workload model
20. Resource allocation
21. Execution orchestrator
22. Test-run state machine
23. WebSocket streaming
24. Metrics ingestion
25. TimescaleDB
26. Live dashboard
27. Transaction analytics
28. Error analytics
29. SLA engine
30. Baselines
31. Comparison
32. Reports
33. Audit logs
34. API keys
35. CI/CD API
36. CLI
37. Prometheus
38. OpenTelemetry
39. Security hardening
40. Kubernetes deployment
41. Backup/restore
42. End-to-end test suite
```

---

# 215. Definition of Architectural Success

The architecture is successful if the following statement is true:

> A user can create a performance test in the browser, assign scripts and workload, select distributed load generators, execute thousands of virtual users, observe the execution in real time, collect transaction and infrastructure metrics, evaluate SLAs, compare the result with a baseline, generate a report, and trigger the same process automatically from a CI/CD pipeline.

That is the core MVP capability that should be built before adding advanced protocols or AI.

---

# 216. Implementation Note

This specification intentionally uses the **LoadRunner Enterprise product model as a functional reference**, but the implementation should use independently developed code, schemas, UI components, protocols, and branding.

OpenText currently markets LoadRunner Enterprise under the **OpenText Enterprise Performance Engineering** name; the underlying product continues to provide centralized enterprise performance testing capabilities.

