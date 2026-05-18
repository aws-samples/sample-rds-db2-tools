import os

class SQLFileReader:
    def __init__(self, filename):
        self.filename = filename
    
    def generateStatement(self):
        with open(self.filename, 'r') as f:
            # read the entire file into memory
            file_contents = f.read()
        
        # split the file contents into SQL statements
        statements = file_contents.split(';')
        
        # remove any leading/trailing whitespace from each statement
        statements = [statement.strip() for statement in statements]
        
        # remove any empty statements
        statements = [statement for statement in statements if statement]
        
        return statements
    
    def print_statements(self):
        statements = self.read_statements()
        for statement in statements:
            print(statement)
