import logging
from flask import Flask, request, render_template_string, jsonify
from rdflib import Graph, Namespace, URIRef, Literal, RDF, RDFS
from rdflib.plugins.sparql import prepareQuery
import requests
from io import StringIO
from pyRdfa import pyRdfa

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Define namespaces
EX = Namespace("http://users.jyu.fi/~tanibir/")

HTML_CLIENT = """
<!DOCTYPE html>
<html>
<head>
    <title>SPARQL and Rule-based Reasoning Service</title>
    <style>
        body { font-family: Arial, sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; }
        textarea { width: 100%; font-family: monospace; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        .error { color: red; }
        pre { white-space: pre-wrap; word-wrap: break-word; background: #f5f5f5; padding: 10px; }
        .hint { color: #666; font-size: 0.9em; margin-top: 5px; }
    </style>
</head>
<body>
    <h1>SPARQL and Rule-based Reasoning Service</h1>
    <form method="POST" action="/run-query">
        <label for="url">URL of XHTML file with RDFa content:</label><br>
        <input type="text" id="url" name="url" placeholder="Enter URL" required style="width: 100%;"
               value="{{ request.form.get('url', '') }}"><br>
        <div class="hint">Example: URL of your XHTML file containing library data</div><br>

        <label for="query_type">Query Type:</label><br>
        <select id="query_type" name="query_type" required>
            <option value="SELECT" {% if request.form.get('query_type') == 'SELECT' %}selected{% endif %}>SELECT</option>
            <option value="CONSTRUCT" {% if request.form.get('query_type') == 'CONSTRUCT' %}selected{% endif %}>CONSTRUCT</option>
            <option value="ASK" {% if request.form.get('query_type') == 'ASK' %}selected{% endif %}>ASK</option>
            <option value="DESCRIBE" {% if request.form.get('query_type') == 'DESCRIBE' %}selected{% endif %}>DESCRIBE</option>
        </select><br><br>

        <label for="rules">Reasoning Rules:</label><br>
        <select id="rule_set" name="rule_set">
            <option value="none" {% if request.form.get('rule_set') == 'none' %}selected{% endif %}>No Rules</option>
            <option value="basic" {% if request.form.get('rule_set') == 'basic' %}selected{% endif %}>Basic Library Rules</option>
            <option value="advanced" {% if request.form.get('rule_set') == 'advanced' %}selected{% endif %}>Advanced Library Rules</option>
            <option value="custom" {% if request.form.get('rule_set') == 'custom' %}selected{% endif %}>Custom Rules</option>
        </select><br>
        <textarea id="custom_rules" name="custom_rules" rows="4" 
                  placeholder="Enter custom rules (if Custom Rules selected)"
                  style="display: {% if request.form.get('rule_set') == 'custom' %}block{% else %}none{% endif %};">{{ request.form.get('custom_rules', '') }}</textarea><br>
        <div class="hint">Basic Rules: Author/Genre inference, Loan patterns<br>
             Advanced Rules: Member categories, Book recommendations</div><br>

        <label for="query">SPARQL Query:</label><br>
        <textarea id="query" name="query" placeholder="Enter your SPARQL query" rows="6" required>{{ request.form.get('query', '') }}</textarea><br>
        <div class="hint">Example queries provided below the results</div><br>

        <button type="submit">Run Query</button>
    </form>

    {% if error %}
        <div class="error">{{ error }}</div>
    {% endif %}

    {% if results %}
        <h2>Query Results</h2>
        {% if query_type == 'SELECT' %}
            <table>
                <thead>
                    <tr>
                        {% for var in headers %}
                            <th>{{ var }}</th>
                        {% endfor %}
                    </tr>
                </thead>
                <tbody>
                    {% for row in results %}
                        <tr>
                            {% for value in row %}
                                <td>{{ value }}</td>
                            {% endfor %}
                        </tr>
                    {% endfor %}
                </tbody>
            </table>
        {% elif query_type == 'ASK' %}
            <p><strong>Result:</strong> {{ results }}</p>
        {% else %}
            <pre>{{ results }}</pre>
        {% endif %}
    {% endif %}

    <script>
        document.getElementById('rule_set').addEventListener('change', function() {
            var customRules = document.getElementById('custom_rules');
            customRules.style.display = this.value === 'custom' ? 'block' : 'none';
        });

        // Restore scroll position after form submission
        window.onload = function() {
            if (window.location.hash) {
                window.scrollTo(0, 0);
            }
        };
    </script>
</body>
</html>
"""

def apply_basic_library_rules(graph):
    """Apply basic library domain rules."""
    # Rule 1: If a book has an author, the author wrote the book
    for book, author in graph.subject_objects(EX.hasAuthor):
        graph.add((author, EX.wrote, book))
        graph.add((book, EX.writtenBy, author))

    # Rule 2: Books with same genre are related
    genre_books = {}
    for book, genre in graph.subject_objects(EX.hasGenre):
        if genre not in genre_books:
            genre_books[genre] = []
        genre_books[genre].append(book)

    for genre, books in genre_books.items():
        for book1 in books:
            for book2 in books:
                if book1 != book2:
                    graph.add((book1, EX.relatedTo, book2))

    # Rule 3: Members with multiple loans are frequent borrowers
    borrower_loans = {}
    for loan, member in graph.subject_objects(EX.borrowedBy):
        if member not in borrower_loans:
            borrower_loans[member] = []
        borrower_loans[member].append(loan)

    for member, loans in borrower_loans.items():
        if len(loans) > 1:
            graph.add((member, RDF.type, EX.FrequentBorrower))

    return graph

def apply_advanced_library_rules(graph):
    """Apply advanced library domain rules."""
    # First apply basic rules
    graph = apply_basic_library_rules(graph)

    # Rule 1: Author expertise based on book genres
    author_genres = {}
    for book in graph.subjects(RDF.type, EX.Book):
        author = next(graph.objects(book, EX.hasAuthor), None)
        genre = next(graph.objects(book, EX.hasGenre), None)
        if author and genre:
            if author not in author_genres:
                author_genres[author] = set()
            author_genres[author].add(genre)

    for author, genres in author_genres.items():
        for genre in genres:
            graph.add((author, EX.hasExpertise, genre))

    # Rule 2: Book recommendations based on borrowing patterns
    member_genres = {}
    for loan in graph.subjects(RDF.type, EX.Loan):
        member = next(graph.objects(loan, EX.borrowedBy), None)
        if member:
            if member not in member_genres:
                member_genres[member] = set()
            member_genres[member].add(next(graph.objects(None, EX.hasGenre), None))

    for member, genres in member_genres.items():
        for genre in genres:
            for book in graph.subjects(EX.hasGenre, genre):
                graph.add((book, EX.recommendedFor, member))

    return graph

def apply_custom_rules(graph, rules_text):
    """Apply custom rules defined by the user."""
    if not rules_text or not rules_text.strip():
        return graph
    
    try:
        # Parse and apply custom rules
        rules = [rule.strip() for rule in rules_text.split('\n') if rule.strip()]
        for rule in rules:
            if rule.startswith('IF') and 'THEN' in rule:
                condition, conclusion = rule.split('THEN')
                condition = condition[2:].strip()
                conclusion = conclusion.strip()
                
                if 'hasAuthor' in condition and 'wrote' in conclusion:
                    for book, author in graph.subject_objects(EX.hasAuthor):
                        graph.add((author, EX.wrote, book))
                
                
    except Exception as e:
        logger.error(f"Error applying custom rules: {e}")
        raise ValueError(f"Error in custom rule processing: {e}")
    
    return graph

def parse_rdfa_from_url(url):
    """Parse RDFa content from URL."""
    graph = Graph()
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or 'utf-8'
        
        processor = pyRdfa()
        rdfa_graph = processor.graph_from_source(StringIO(response.text))
        
        for triple in rdfa_graph:
            graph.add(triple)
            
        return graph
        
    except Exception as e:
        logger.error(f"Error parsing RDFa: {e}", exc_info=True)
        raise ValueError(f"Error parsing RDFa: {e}")

@app.route("/", methods=["GET"])
def index():
    return render_template_string(HTML_CLIENT)

@app.route("/run-query", methods=["POST"])
def run_query():
    url = request.form.get("url")
    query = request.form.get("query")
    query_type = request.form.get("query_type", "SELECT")
    rule_set = request.form.get("rule_set", "none")
    custom_rules = request.form.get("custom_rules", "")

    try:
        # Parse RDFa and apply selected reasoning rules
        graph = parse_rdfa_from_url(url)
        
        if rule_set == "basic":
            graph = apply_basic_library_rules(graph)
        elif rule_set == "advanced":
            graph = apply_advanced_library_rules(graph)
        elif rule_set == "custom":
            graph = apply_custom_rules(graph, custom_rules)

        # Execute query based on type
        if query_type == "SELECT":
            result = graph.query(query)
            headers = result.vars
            results = [[str(row[var]) for var in headers] for row in result]
            return render_template_string(HTML_CLIENT, 
                                       results=results, 
                                       headers=[str(var) for var in headers],
                                       query_type=query_type)

        elif query_type == "CONSTRUCT":
            result = graph.query(query)
            constructed_graph = Graph()
            for triple in result:
                constructed_graph.add(triple)
            results = constructed_graph.serialize(format='turtle')
            if isinstance(results, bytes):
                results = results.decode('utf-8')
            return render_template_string(HTML_CLIENT, 
                                       results=results,
                                       query_type=query_type)

        elif query_type == "ASK":
            result = graph.query(query)
            results = str(result.askAnswer)
            return render_template_string(HTML_CLIENT, 
                                       results=results,
                                       query_type=query_type)

        elif query_type == "DESCRIBE":
            result = graph.query(query)
            described_graph = Graph()
            for triple in result:
                described_graph.add(triple)
            results = described_graph.serialize(format='turtle')
            if isinstance(results, bytes):
                results = results.decode('utf-8')
            return render_template_string(HTML_CLIENT, 
                                       results=results,
                                       query_type=query_type)

    except Exception as e:
        logger.error(f"Error processing query: {e}", exc_info=True)
        return render_template_string(HTML_CLIENT, error=f"Error: {str(e)}")

if __name__ == "__main__":
    app.run(debug=True)