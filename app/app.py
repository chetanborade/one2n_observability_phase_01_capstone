# OpenTelemetry setup
from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
import logging

# Initialize OpenTelemetry
resource = Resource.create({"service.name": "flask-todo-app"})

# Setup tracing
trace_provider = TracerProvider(resource=resource)
trace.set_tracer_provider(trace_provider)
trace_provider.add_span_processor(BatchSpanProcessor(
    OTLPSpanExporter(endpoint="http://signoz-otel-collector:4318/v1/traces")
))

# Setup metrics
metrics_provider = MeterProvider(
    resource=resource, 
    metric_readers=[PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint="http://signoz-otel-collector:4318/v1/metrics"),
        export_interval_millis=30000
    )]
)
metrics.set_meter_provider(metrics_provider)

# Setup logging
logger_provider = LoggerProvider(resource=resource)
logger_provider.add_log_record_processor(BatchLogRecordProcessor(
    OTLPLogExporter(endpoint="http://signoz-otel-collector:4318/v1/logs")
))
logging.basicConfig(level=logging.INFO, handlers=[LoggingHandler(logger_provider=logger_provider)])
logger = logging.getLogger(__name__)

# Flask app
from flask import Flask, jsonify, request
import psycopg2
from psycopg2.extras import RealDictCursor
import redis
import json
from datetime import datetime

app = Flask(__name__)

# Instrument all components
FlaskInstrumentor().instrument_app(app)
Psycopg2Instrumentor().instrument()
RedisInstrumentor().instrument()

# Custom metrics
meter = metrics.get_meter(__name__)
todo_counter = meter.create_counter("todos_created_total")

def get_db_connection():
    return psycopg2.connect(
        host='postgres', database='tododb', 
        user='todouser', password='todopass', port=5432
    )

def get_redis_client():
    return redis.Redis(host='redis', port=6379, decode_responses=True)

def serialize_datetime(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")

@app.route('/')
def home():
    return "Hello, TODO App!"

@app.route('/test-db')
def test_db():
    try:
        conn = get_db_connection()
        conn.close()
        return jsonify({'status': 'Database connection successful'})
    except Exception as e:
        logger.error(f"Database connection failed: {str(e)}")
        return jsonify({'status': 'Database connection failed', 'error': str(e)})

@app.route('/todos', methods=['POST'])
def create_todo():
    todo_counter.add(1)
    data = request.get_json()
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute('INSERT INTO todos (title) VALUES (%s) RETURNING *', (data['title'],))
    todo = cursor.fetchone()
    conn.commit()
    cursor.close()
    conn.close()
    
    get_redis_client().delete('all_todos')
    logger.info(f"Created todo: {data['title']}")
    
    return jsonify(json.loads(json.dumps(todo, default=serialize_datetime))), 201

@app.route('/todos', methods=['GET'])
def get_todos():
    redis_client = get_redis_client()
    cached_todos = redis_client.get('all_todos')
    
    if cached_todos and isinstance(cached_todos, str):
        return jsonify(json.loads(cached_todos))
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute('SELECT * FROM todos')
    todos = cursor.fetchall()
    cursor.close()
    conn.close()
    
    redis_client.set('all_todos', json.dumps(todos, default=serialize_datetime), ex=60)
    return jsonify(todos)

@app.route('/todos/<int:id>', methods=['GET'])
def get_todo(id):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute('SELECT * FROM todos WHERE id = %s', (id,))
    todo = cursor.fetchone()
    cursor.close()
    conn.close()
    return jsonify(json.loads(json.dumps(todo, default=serialize_datetime)))

@app.route('/todos/<int:id>', methods=['PUT'])
def update_todo(id):
    data = request.get_json()
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute('UPDATE todos SET title = %s, completed = %s WHERE id = %s RETURNING *',
                   (data.get('title'), data.get('completed'), id))
    todo = cursor.fetchone()
    conn.commit()
    cursor.close()
    conn.close()
    
    get_redis_client().delete('all_todos')
    return jsonify(json.loads(json.dumps(todo, default=serialize_datetime)))

@app.route('/todos/<int:id>', methods=['DELETE'])
def delete_todo(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM todos WHERE id = %s', (id,))
    conn.commit()
    cursor.close()
    conn.close()
    
    get_redis_client().delete('all_todos')
    return jsonify({'message': 'deleted'})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)