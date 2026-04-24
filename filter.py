import sqlite3
import concurrent.futures

def search_contacts(search_term: str) -> str:
    db_path = "directory.sqlite3"
    output = ""
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        query = """
            SELECT name, email, employee_id 
            FROM contacts 
            WHERE email LIKE ?
            OR employee_id LIKE ?
            OR name LIKE ? 
        """
        
        term = search_term 
        
        cursor.execute(query, (term, term, term))
        results = cursor.fetchall()
        
        # If we find results, build a string instead of printing
        if results:
            output += f"\nFound {len(results)} matching record(s) for '{search_term}':\n"
            output += "-" * 40 + "\n"
            for row in results:
                output += f"Name: {row[0]}\n"
                output += f"Email: {row[1]}\n"
                output += f"ID: {row[2]}\n"
                output += "-" * 40 + "\n"
                
    except sqlite3.Error as e:
        output = f"Database error: {e}"
    finally:
        if 'conn' in locals():
            conn.close()
            
    return output

if __name__ == "__main__":
    print("--- Directory Search ---")

    queries = [f"p2025{'%04d' % (i,)}@pilani.bits-pilani.ac.in" for i in range(2000)]

    try:
        # multithread using ThreadPoolExecutor to search for all queries concurrently
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            # executor.map preserves the original order of the 'queries' list
            results = executor.map(search_contacts, queries)
            
            # Print the results one by one in order
            for result_text in results:
                if result_text:  # Only print if the thread actually found a match
                    print(result_text, end="")

    except KeyboardInterrupt:
        print("\nExiting search. Goodbye!")