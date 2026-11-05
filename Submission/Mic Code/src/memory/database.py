import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'aurus_memory.db')

def get_connection():
    return sqlite3.connect(DB_PATH)

def init_db():
    # Ensure directory exists if needed, though here it's two levels up
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    conn = get_connection()
    cursor = conn.cursor()
    
    # Users Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            relationship_strength REAL DEFAULT 0.5
        )
    ''')
    
    # Memories Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            memory_type TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES Users(id)
        )
    ''')
    
    # Events Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT,
            description TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Interactions Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            interaction_type TEXT,
            details TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES Users(id)
        )
    ''')
    
    # Preferences Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            preference_key TEXT,
            preference_value TEXT,
            FOREIGN KEY(user_id) REFERENCES Users(id),
            UNIQUE(user_id, preference_key)
        )
    ''')
    
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("Database initialized.")
