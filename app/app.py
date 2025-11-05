from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor

# Set up tracing
trace.set_tracer_provider(TracerProvider())
tracer_provider = trace.get_tracer_provider()

# Configure exporter to send to SigNoz
otlp_exporter = OTLPSpanExporter(
    endpoint="http://signoz-otel-collector:4317",  # SigNoz collector
    insecure=True
)
tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

from flask import Flask, jsonify, request
import psycopg2
from psycopg2.extras import RealDictCursor
import redis
import json
from datetime import datetime

app = Flask(__name__)

def get_db_connection():
    conn = psycopg2.connect(
        host='postgres',
        database='tododb',
        user='todouser',
        password='todopass',
        port=5432
    )
    return conn

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
        cursor = conn.cursor()
        cursor.close()
        conn.close()
        return jsonify({'status': 'Database connection successful'})
    except Exception as e:
        return jsonify({'status': 'Database connection failed', 'error': str(e)})

@app.route('/todos', methods=['POST'])
def create_todo():
    data = request.get_json()
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute('INSERT INTO todos (title) VALUES (%s) RETURNING *', (data['title'],))
    todo = cursor.fetchone()
    conn.commit()
    cursor.close()
    conn.close()
    
    redis_client = get_redis_client()
    redis_client.delete('all_todos')
    
    return jsonify(json.loads(json.dumps(todo, default=serialize_datetime))), 201

@app.route('/todos', methods=['GET'])
def get_todos():
    redis_client = get_redis_client()
    
    cached_todos = redis_client.get('all_todos')
    if cached_todos:
        return jsonify(json.loads(cached_todos))
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute('SELECT * FROM todos')
    todos = cursor.fetchall()
    cursor.close()
    conn.close()
    
    redis_client.set('all_todos', json.dumps(todos, default=serialize_datetime), ex=60)
    
    return jsonify(json.loads(json.dumps(todos, default=serialize_datetime)))

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
    
    redis_client = get_redis_client()
    redis_client.delete('all_todos')
    
    return jsonify(json.loads(json.dumps(todo, default=serialize_datetime)))

@app.route('/todos/<int:id>', methods=['DELETE'])
def delete_todo(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM todos WHERE id = %s', (id,))
    conn.commit()
    cursor.close()
    conn.close()
    
    redis_client = get_redis_client()
    redis_client.delete('all_todos')
    
    return jsonify({'message': 'deleted'})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)