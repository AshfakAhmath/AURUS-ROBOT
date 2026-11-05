from .database import get_connection, init_db

class MemoryManager:
    def __init__(self):
        init_db()
        
    def add_user(self, name):
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO Users (name) VALUES (?)", (name,))
        conn.commit()
        
        cursor.execute("SELECT id FROM Users WHERE name = ?", (name,))
        user_id = cursor.fetchone()[0]
        conn.close()
        return user_id
        
    def log_interaction(self, user_name, interaction_type, details):
        user_id = self.add_user(user_name)
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO Interactions (user_id, interaction_type, details) VALUES (?, ?, ?)",
            (user_id, interaction_type, details)
        )
        conn.commit()
        conn.close()
        
    def add_memory(self, user_name, memory_type, content):
        user_id = self.add_user(user_name)
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO Memories (user_id, memory_type, content) VALUES (?, ?, ?)",
            (user_id, memory_type, content)
        )
        conn.commit()
        conn.close()
        
    def log_event(self, event_type, description):
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO Events (event_type, description) VALUES (?, ?)",
            (event_type, description)
        )
        conn.commit()
        conn.close()
        
    def get_recent_memories(self, limit=5):
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT content FROM Memories ORDER BY timestamp DESC LIMIT ?", 
            (limit,)
        )
        rows = cursor.fetchall()
        conn.close()
        return [row[0] for row in rows]
        
    def get_recent_events(self, limit=5):
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT description FROM Events ORDER BY timestamp DESC LIMIT ?", 
            (limit,)
        )
        rows = cursor.fetchall()
        conn.close()
        return [row[0] for row in rows]
