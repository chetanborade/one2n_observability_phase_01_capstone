from flask import Flask, jsonify, request
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)

def get_db_connection():
    conn = psycopg2.connect(
        host='127.0.0.1',
        database='tododb',
        user='todouser',
        password='todopass',
        port=5433
    )
    return conn

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
    return jsonify(todo), 201

@app.route('/todos', methods=['GET'])
def get_todos():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute('SELECT * FROM todos')
    todos = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify(todos)

# Get one todo
@app.route('/todos/<int:id>', methods=['GET'])
def get_todo(id):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute('SELECT * FROM todos WHERE id = %s', (id,))
    todo = cursor.fetchone()
    cursor.close()
    conn.close()
    return jsonify(todo)

# Update todo
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
    return jsonify(todo)

# Delete todo
@app.route('/todos/<int:id>', methods=['DELETE'])
def delete_todo(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM todos WHERE id = %s', (id,))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'message': 'deleted'})

if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5000)