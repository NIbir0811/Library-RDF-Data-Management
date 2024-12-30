import logging
from flask import Flask, request, render_template_string, jsonify
from rdflib import Graph, Namespace, URIRef, Literal, RDF, RDFS
from rdflib.plugins.sparql import prepareQuery
from rdflib.term import Node, Variable, BNode
import requests
from io import StringIO
from pyRdfa import pyRdfa

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Define namespaces
EX = Namespace("http://users.jyu.fi/~tanibir/")
LOG = Namespace("http://www.w3.org/2000/10/swap/log#")

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

        <label for="rule_set">Reasoning Rules:</label><br>
        <select id="rule_set" name="rule_set">
            <option value="none" {% if request.form.get('rule_set') == 'none' %}selected{% endif %}>No Rules</option>
            <option value="basic" {% if request.form.get('rule_set') == 'basic' %}selected{% endif %}>Basic Library Rules</option>
            <option value="advanced" {% if request.form.get('rule_set') == 'advanced' %}selected{% endif %}>Advanced Library Rules</option>
            <option value="custom" {% if request.form.get('rule_set') == 'custom' %}selected{% endif %}>Custom Rules</option>
        </select><br>
        
        <textarea id="custom_rules" name="custom_rules" rows="4" 
                  placeholder="Enter custom rules (if Custom Rules selected)"
                  style="display: {% if request.form.get('rule_set') == 'custom' %}block{% else %}none{% endif %};">{{ request.form.get('custom_rules', '') }}</textarea><br>
        
        <label for="cwm_rules">CWM Rules (N3 format):</label><br>
        <textarea id="cwm_rules" name="cwm_rules" rows="4" 
                  placeholder="Enter CWM rules in N3 format (e.g., ?x hasAuthor ?y => ?y wrote ?x)"
        >{{ request.form.get('cwm_rules', '') }}</textarea><br>
        <div class="hint">Example: ?x ex:hasAuthor ?y => ?y ex:wrote ?x</div><br>

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
    </script>
</body>
</html>
"""

def parse_cwm_rule(rule_text):
    """Parse a CWM rule in N3 format."""
    antecedent = []
    consequent = []
    
    try:
        # Split into if/then parts
        parts = rule_text.split("=>")
        if len(parts) != 2:
            raise ValueError("Rule must contain exactly one '=>'")
            
        # Parse antecedent (if part)
        ant_triples = parts[0].strip().split(".")
        for triple in ant_triples:
            if triple.strip():
                s, p, o = [x.strip() for x in triple.split()]
                antecedent.append((s, p, o))
                
        # Parse consequent (then part)
        cons_triples = parts[1].strip().split(".")
        for triple in cons_triples:
            if triple.strip():
                s, p, o = [x.strip() for x in triple.split()]
                consequent.append((s, p, o))
                
        return antecedent, consequent
    except Exception as e:
        logger.error(f"Error parsing CWM rule: {e}")
        raise ValueError(f"Invalid CWM rule format: {e}")

def apply_cwm_rules(graph, rules_text):
    """Apply CWM rules to the graph."""
    if not rules_text:
        return graph
    
    # Create a new graph and copy all triples from the original graph
    new_graph = Graph()
    for triple in graph:
        new_graph.add(triple)
    
    rules = [r.strip() for r in rules_text.split('\n') if r.strip()]
    
    for rule in rules:
        try:
            antecedent, consequent = parse_cwm_rule(rule)
            
            # Create SPARQL query from antecedent
            var_map = {}
            query_parts = []
            for s, p, o in antecedent:
                if s.startswith('?'):
                    if s not in var_map:
                        var_map[s] = Variable(s[1:])
                if o.startswith('?'):
                    if o not in var_map:
                        var_map[o] = Variable(o[1:])
                
                # Handle predicates that might be variables
                if p.startswith('?'):
                    if p not in var_map:
                        var_map[p] = Variable(p[1:])
                    query_parts.append(f"{s} ?{var_map[p].n3()} {o}")
                else:
                    # Add namespace prefix for predicates if they use the EX namespace
                    if not p.startswith(('http://', 'https://')):
                        p = f"ex:{p}"
                    query_parts.append(f"{s} {p} {o}")
            
            query_str = """
                PREFIX ex: <http://users.jyu.fi/~tanibir/>
                SELECT * WHERE { 
                    """ + " . ".join(query_parts) + " }"
            
            # Execute query and apply consequences
            results = new_graph.query(query_str)
            
            for result in results:
                # Create binding dictionary
                bindings = {}
                for var_name, var in var_map.items():
                    bindings[var_name] = result[var_map[var_name]]
                
                # Apply consequent with bindings
                for s, p, o in consequent:
                    # Replace variables with their bindings
                    if s.startswith('?'):
                        actual_s = bindings.get(s, None)
                        if actual_s is None:
                            continue
                    else:
                        actual_s = URIRef(EX + s)
                    
                    # Handle predicates
                    if p.startswith('?'):
                        actual_p = bindings.get(p, None)
                        if actual_p is None:
                            continue
                    else:
                        actual_p = URIRef(EX + p)
                    
                    if o.startswith('?'):
                        actual_o = bindings.get(o, None)
                        if actual_o is None:
                            continue
                    else:
                        actual_o = URIRef(EX + o)
                    
                    new_graph.add((actual_s, actual_p, actual_o))
                    
        except Exception as e:
            logger.error(f"Error applying rule {rule}: {e}")
            continue
    
    return new_graph

def apply_basic_library_rules(graph):
    """Apply basic library domain rules."""
    # Rule 1: If a book has an author, the author wrote the book
    for book, author in graph.subject_objects(EX.hasAuthor):
        graph.add((author, EX.wrote, book))

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

    return graph

def apply_advanced_library_rules(graph):
    """Apply advanced library domain rules."""
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

    # Rule 2: Book recommendations based on user preferences
    for user in graph.subjects(RDF.type, EX.User):
        preferred_genre = next(graph.objects(user, EX.prefersGenre), None)
        if preferred_genre:
            for book in graph.subjects(EX.hasGenre, preferred_genre):
                graph.add((book, EX.recommendedFor, user))

    return graph

def apply_custom_rules(graph, rules_text):
    """Apply custom rules defined by the user."""
    if not rules_text:
        return graph
    
    try:
        rules = [rule.strip() for rule in rules_text.split('\n') if rule.strip()]
        for rule in rules:
            if "=>" in rule:
                condition, action = rule.split("=>")
                condition = condition.strip()
                action = action.strip()
                
                # Execute the rule based on the condition
                if "hasAuthor" in condition and "wrote" in action:
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
    cwm_rules = request.form.get("cwm_rules", "")

    try:
        # Parse RDFa
        graph = parse_rdfa_from_url(url)
        
        # Apply selected reasoning rules
        if rule_set == "basic":
            graph = apply_basic_library_rules(graph)
        elif rule_set == "advanced":
            graph = apply_advanced_library_rules(graph)
        elif rule_set == "custom":
            graph = apply_custom_rules(graph, custom_rules)
        
        # Apply CWM rules if provided
        if cwm_rules:
            graph = apply_cwm_rules(graph, cwm_rules)

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
