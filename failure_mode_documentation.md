# Failure Mode Documentation: Container Networking Exhaustion

## Executive Summary

This document describes a critical failure mode discovered in our containerized Flask todo application where high concurrent traffic overwhelms Docker's internal networking infrastructure, causing complete service outage despite healthy underlying services.

---

## Failure Mode Overview

### Failure Type: Container Networking Exhaustion Under High Load
### Severity: Critical (Complete Service Outage)
### MTTR: ~2 minutes (requires application restart)
### Likelihood: Medium (common during traffic spikes)

---

## Technical Description

### What is Container Networking Exhaustion?

Container networking exhaustion occurs when the Docker networking stack becomes overwhelmed by sudden concurrent connection attempts, causing DNS resolution failures and network timeouts before requests even reach the target services.

### System Architecture Context
```
┌─────────────┐    ┌──────────────┐    ┌─────────────┐
│   Client    │───▶│ Flask App    │───▶│ PostgreSQL  │
│  (curl)     │    │ (Container)  │    │ (Container) │
└─────────────┘    └──────────────┘    └─────────────┘
                          │
                          ▼
                   ┌──────────────┐
                   │ Docker       │
                   │ Bridge       │
                   │ Network      │
                   │ + DNS        │
                   └──────────────┘
```

---

## Failure Trigger and Root Cause

### Trigger Event
- **Sudden traffic spike**: 120 concurrent HTTP requests
- **No rate limiting**: All requests processed simultaneously
- **Instant overload**: No graceful degradation

### Root Cause Analysis

#### 1. DNS Resolution Bottleneck
```python
# Each request executes:
def get_db_connection():
    return psycopg2.connect(host='postgres', ...)  # DNS lookup required
```

**Problem**: 120 simultaneous DNS lookups for "postgres" hostname overwhelm Docker's internal DNS resolver.

#### 2. Container Resource Limits
```bash
# No limits set in docker-compose.yml
flask-app:
  # Missing:
  # deploy:
  #   resources:
  #     limits:
  #       memory: 512M
  #       cpus: '0.5'
```

**Problem**: Flask container can spawn unlimited threads/processes for concurrent requests.

#### 3. Network Namespace Exhaustion
- **Docker bridge network**: Limited concurrent connection handling
- **iptables rules**: Processing overhead for each connection
- **Socket descriptors**: Kernel limits on open file descriptors

#### 4. Application Design Issues
- **No connection pooling**: Each request creates new database connection
- **Blocking I/O**: DNS resolution blocks request threads
- **No circuit breaker**: No protection against cascade failures

---

## Failure Progression Timeline

### T+0 seconds: Attack Launch
```bash
for i in {1..120}; do curl -s http://localhost:5000/todos & done
```
- 120 curl processes launched simultaneously
- Each targets Flask app `/todos` endpoint

### T+1 second: DNS Overload
```
120 requests → Flask app → 120 DNS lookups for "postgres"
Docker DNS resolver: Queue overflow
```

### T+2 seconds: Network Failure
```
DNS Resolution: FAILED
Error: "could not translate host name 'postgres' to address"
```

### T+3 seconds: Service Outage
```
HTTP Response: Connection Refused (Code 000)
Database: Still healthy (only 6/100 connections used)
Application: Unresponsive
```

### T+300 seconds: Manual Recovery
```bash
docker restart todo_flask_app
```

---

## Symptoms and Detection

### User-Visible Symptoms
- **HTTP Connection Refused**: Unable to reach application
- **Complete service outage**: All endpoints unresponsive
- **No error pages**: Connection fails before reaching app

### Technical Symptoms
```python
# Application Logs
psycopg2.OperationalError: could not translate host name "postgres" to address: Name or service not known

# HTTP Response
Response Code: 000 (Connection Failed)

# Container Status
Container: Running (but unresponsive)
```

### Monitoring Indicators

#### Traces (SigNoz)
```yaml
Normal State: Regular trace generation
Failure State: Complete absence of traces
Recovery: Traces resume after restart
```

#### Metrics
```yaml
HTTP Error Rate: Spike to 100%
Response Time: Timeout (no response)
Database Connections: Remains normal (6/100)
```

#### Logs
```yaml
Pattern: "could not translate host name"
Frequency: Multiple rapid occurrences
Context: Concurrent request processing
```

---

## Impact Assessment

### Business Impact
- **Complete service unavailability**: Users cannot access application
- **Data integrity**: Not affected (database remains healthy)
- **User experience**: Total failure, no graceful degradation

### Technical Impact
- **Cascade failure**: Network → DNS → Application
- **False diagnosis risk**: Appears as database issue
- **Manual intervention**: Requires restart for recovery

### Severity Factors
- **Scope**: Entire application
- **Duration**: Until manual restart
- **Detection**: May be missed as database issue
- **Recovery**: Simple but requires manual action

---

## Detection and Alerting Strategy

### Primary Alert: Application Unresponsive
```yaml
Alert Name: Todo App Network Failure
Type: Trace-based alert
Condition: No traces from flask-todo-app for 2 minutes
Threshold: Count < 1
Duration: 2 minutes
Severity: Critical
```

### Secondary Alert: High Error Rate
```yaml
Alert Name: Complete Service Outage
Type: Metrics-based alert  
Condition: HTTP error rate > 95%
Threshold: 95%
Duration: 1 minute
Severity: Critical
```

### Monitoring Queries
```sql
-- Trace absence detection
COUNT(traces) WHERE service.name = 'flask-todo-app' 

-- Error rate monitoring
(COUNT(errors) / COUNT(total_requests)) * 100
```

---

## Diagnosis Playbook

### Step 1: Confirm Service Status
```bash
# Test application responsiveness
curl -w "Response Code: %{http_code}\n" http://localhost:5000/

# Expected: Code 000 (connection failed)
```

### Step 2: Check Container Health
```bash
# Container running but unresponsive?
docker ps | grep flask-app
docker logs todo_flask_app --tail 20

# Look for: DNS resolution errors
```

### Step 3: Verify Backend Services
```bash
# Database still healthy?
docker exec todo_postgres_db psql -U todouser -c "SELECT 1;"

# Redis still healthy?  
docker exec todo_redis redis-cli ping
```

### Step 4: Network Diagnosis
```bash
# DNS resolution test
docker exec todo_flask_app nslookup postgres

# Network connectivity test
docker exec todo_flask_app ping postgres
```

---

## Recovery Procedures

### Immediate Recovery
```bash
# 1. Restart the application container
docker restart todo_flask_app

# 2. Verify recovery
curl http://localhost:5000/test-db

# Expected: {"status": "Database connection successful"}
```

### Recovery Verification
```bash
# Test full functionality
curl http://localhost:5000/todos              # GET works
curl -X POST -H "Content-Type: application/json" \
     -d '{"title":"Recovery test"}' \
     http://localhost:5000/todos               # POST works
```

### Recovery Time
- **Manual restart**: ~30 seconds
- **Service restoration**: ~60 seconds  
- **Full verification**: ~120 seconds
- **Total MTTR**: ~2-3 minutes

---

## Prevention Strategies

### 1. Rate Limiting
```python
from flask_limiter import Limiter

limiter = Limiter(
    app,
    key_func=get_remote_address,
    default_limits=["100 per minute"]
)

@app.route('/todos')
@limiter.limit("10 per second")  # Prevent traffic spikes
def get_todos():
    # ...
```

### 2. Connection Pooling
```python
from psycopg2 import pool

# Create connection pool at startup
connection_pool = psycopg2.pool.SimpleConnectionPool(
    1, 20,  # min=1, max=20 connections
    host='postgres',
    database='tododb',
    user='todouser', 
    password='todopass'
)

def get_db_connection():
    return connection_pool.getconn()  # Reuse connections
```

### 3. Container Resource Limits
```yaml
# docker-compose.yml
flask-app:
  deploy:
    resources:
      limits:
        memory: 512M
        cpus: '0.5'
      reservations:
        memory: 256M
        cpus: '0.25'
```

### 4. Circuit Breaker Pattern
```python
from circuitbreaker import circuit

@circuit(failure_threshold=5, recovery_timeout=30)
def get_db_connection():
    # Fail fast if database connections failing
    return psycopg2.connect(...)
```

### 5. Health Check Implementation
```python
@app.route('/health')
def health_check():
    try:
        # Quick health check without full DB connection
        return {"status": "healthy", "timestamp": datetime.now()}
    except:
        return {"status": "unhealthy"}, 500
```

---

## Load Testing Recommendations

### Gradual Load Testing
```bash
# Test sustainable load levels
for concurrency in 5 10 20 50; do
  echo "Testing with $concurrency concurrent requests"
  for i in $(seq 1 $concurrency); do
    curl http://localhost:5000/todos &
  done
  wait
  sleep 10
done
```

### Stress Testing Protocol
```bash
# Find breaking point gradually
ab -n 1000 -c 10 http://localhost:5000/todos   # 10 concurrent
ab -n 1000 -c 25 http://localhost:5000/todos   # 25 concurrent  
ab -n 1000 -c 50 http://localhost:5000/todos   # 50 concurrent
# Increase until failure point identified
```

---

## Demo Script for Stakeholders

### Setup
```bash
# Verify normal operation
curl http://localhost:5000/todos  # Should return todo list
```

### Failure Simulation
```bash
# Launch traffic spike
echo "Simulating traffic spike..."
for i in {1..120}; do curl -s http://localhost:5000/todos > /dev/null & done

# Wait for failure
sleep 5

# Demonstrate failure
curl http://localhost:5000/  # Should fail with connection refused
```

### Show Monitoring
- **SigNoz**: Show absence of traces
- **Alerts**: Show firing alerts  
- **Logs**: Show DNS resolution errors

### Recovery Demonstration
```bash
# Recover service
docker restart todo_flask_app
sleep 5

# Verify recovery
curl http://localhost:5000/test-db  # Should return success
```

---

**Document Version**: 1.0  
**Date**: November 10, 2025  
**Author**: Observability Team  
**Review Date**: November 10, 2026