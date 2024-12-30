import logging
from flask import Flask, request, render_template_string, jsonify
from rdflib import Graph, Namespace, URIRef, Literal
from rdflib.plugins.sparql import prepareQuery
import requests
import html5lib
from io import BytesIO
from io import StringIO
from pyRdfa import pyRdfa

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# HTML client template
HTML_CLIENT = """
<!DOCTYPE html>
<html>
<head>
    <title>SPARQL and Rule-based Reasoning Service</title>
    <style>
        body { font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }
        textarea { width: 100%; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        .error { color: red; }
    </style>
</head>
<body>
    <h1>SPARQL and Rule-based Reasoning Service</h1>
    <form method="POST" action="/run-query">
        <label for="url">URL of XHTML file with RDFa content:</label><br>
        <input type="text" id="url" name="url" placeholder="Enter URL" required style="width: 100%;"><br><br>

        <label for="rules">Rules (Optional, simple inference):</label><br>
        <textarea id="rules" name="rules" placeholder="Basic inference rules" rows="4"></textarea><br><br>

        <label for="query">SPARQL Query:</label><br>
        <textarea id="query" name="query" placeholder="SELECT ?s ?p ?o WHERE { ?s ?p ?o }" rows="4" required></textarea><br><br>

        <button type="submit">Run Query</button>
    </form>

    {% if error %}
        <div class="error">{{ error }}</div>
    {% endif %}

    {% if results %}
        <h2>Query Results</h2>
        <table>
            <thead>
                <tr>
                    {% for var in headers %}
                        <th>{{ var }}</th>
                    {% endfor %}
                </tr>
            </thead>
            <tbody>
                {% if results %}
                    {% for row in results %}
                        <tr>
                            {% for header in headers %}
                                <td>
                                    {% if loop.index0 < row|length %}
                                        {{ row[loop.index0] }}
                                    {% else %}
                                        <em>N/A</em>
                                    {% endif %}
                                </td>
                            {% endfor %}
                        </tr>
                    {% endfor %}
                {% else %}
                    <tr>
                        <td colspan="{{ headers|length }}">No results found</td>
                    </tr>
                {% endif %}
            </tbody>
        </table>
    {% endif %}
</body>
</html>

"""


def parse_rdfa_from_url(url):
   
    graph = Graph()
    try:
        # Fetch the content with explicit encoding and error handling
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        # Detect encoding
        response.encoding = response.apparent_encoding or 'utf-8'

        # Use pyRdfa to parse RDFa from HTML content
        processor = pyRdfa()
        rdfa_graph = processor.graph_from_source(StringIO(response.text))  

        # Convert RDFa graph to rdflib Graph
        for triple in rdfa_graph:
            graph.add(triple)

        logger.info(f"Successfully parsed RDFa from {url}")
        logger.info(f"Parsed graph contains {len(graph)} triples")
        
        # debugging
        for s, p, o in list(graph)[:5]:
            logger.info(f"Sample triple: {s} {p} {o}")

        return graph

    except requests.RequestException as e:
        logger.error(f"Network error fetching URL {url}: {e}")
        raise ValueError(f"Could not fetch URL: Network error - {e}")
    except Exception as e:
        logger.error(f"Comprehensive error parsing RDFa: {e}", exc_info=True)
        raise ValueError(f"Error parsing RDFa: {e}")


def apply_custom_rules(graph, rules):
    """
    Apply custom rules to extend the graph.
    
    Args:
        graph (rdflib.Graph): Input RDF graph
        rules (str): Simple rules description
    
    Returns:
        rdflib.Graph: Extended graph
    """
    if not rules or not rules.strip():
        return graph

    try:
        # Basic rule processing (simplified)
        namespace = Namespace("http://example.org/inference#")
        
        # Example: Simple transitive property inference
        for s, p, o in list(graph):
            # Example rule: If ?x knows ?y and ?y knows ?z, then ?x indirectKnows ?z
            if str(p).endswith('knows'):
                for _, p2, z in graph.triples((o, None, None)):
                    if str(p2).endswith('knows'):
                        graph.add((s, namespace.indirectKnows, z))
        
        logger.info("Custom rules applied successfully")
        return graph
    except Exception as e:
        logger.error(f"Error applying custom rules: {e}")
        raise ValueError(f"Error in rule processing: {e}")

@app.route("/", methods=["GET"])
def index():
    return render_template_string(HTML_CLIENT)

@app.route("/run-query", methods=["POST"])
def run_query():
    
    url = request.form.get("url")
    rules = request.form.get("rules")
    query = request.form.get("query")
    query_type = request.form.get("query_type", "SELECT")

    try:
        # Parse RDFa content
        graph = parse_rdfa_from_url(url)
        
        # Apply custom rules if provided
        graph = apply_custom_rules(graph, rules)
        
        # Execute SPARQL query based on type
        if query_type == 'SELECT':
            prepared_query = prepareQuery(query)
            result = graph.query(prepared_query)
            
            # Parse result into table format
            headers = result.vars
            results = []
            for binding in result:
                row = []
                for var in headers:
                    row.append(str(binding.get(var, 'N/A')))
                results.append(row)
            
            return render_template_string(HTML_CLIENT, 
                                          results=results, 
                                          headers=[str(var) for var in headers],
                                          query_type=query_type)
        
        elif query_type == 'CONSTRUCT':
            # Serialize the constructed graph
            # result = graph.query(query)
            # results = result.serialize(format='turtle')
            
            # return render_template_string(HTML_CLIENT, 
            #                               results=results,
            #                               query_type=query_type)
            result = graph.query(query)
            constructed_graph = Graph()
            for triple in result:
                constructed_graph.add(triple)
            results = constructed_graph.serialize(format='turtle').decode("utf-8")

            return render_template_string(HTML_CLIENT, results=results)
        
        elif query_type == 'ASK':
        #     # Perform ASK query
        #     result = graph.query(query)
        #     results = str(bool(result))
            
        #     return render_template_string(HTML_CLIENT, 
        #                                   results=results,
        #                                   query_type=query_type)

            result = graph.query(query)
            results = str(bool(result))

            return render_template_string(HTML_CLIENT, results=results)
        
        
            
        elif query_type == 'DESCRIBE':
            # Implement DESCRIBE query
            # prepared_query = prepareQuery(query)
            # result_graph = graph.query(prepared_query)
            # results = result_graph.serialize(format='turtle')
            
            # return render_template_string(HTML_CLIENT, 
            #                               results=results,
            #                               query_type=query_type)
            result = graph.query(query)
            described_graph = Graph()
            for triple in result:
                described_graph.add(triple)
            results = described_graph.serialize(format='turtle').decode("utf-8")

            return render_template_string(HTML_CLIENT, results=results)
    
    except ValueError as ve:
        return render_template_string(HTML_CLIENT, error=str(ve))
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return render_template_string(HTML_CLIENT, error="An unexpected error occurred")
    

    

if __name__ == "__main__":
    app.run(host='127.0.0.1', port=5000, debug=True)