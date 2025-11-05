# app.py
import os
import requests
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from PyPDF2 import PdfReader
import docx2txt
import traceback

load_dotenv()

app = Flask(__name__, static_folder='static', template_folder='templates')
CORS(app)

# Use variável de ambiente quando disponível. Caso não tenha, use a chave fornecida (apenas para dev).
DIFY_API_KEY = os.getenv("DIFY_API_KEY", "app-aO3gWXIMk1OhqQD9nfE4L1ep")
DIFY_API_URL = "https://api.dify.ai/v1/chat-messages"

# Página principal (renderiza chatbot.html, coloque o arquivo em templates/chatbot.html)
@app.route('/')
def index():
    return render_template('chatbot.html',
        title='Meu Chatbot Personalizado',
        bot_name='Assistente Virtual',
        bot_icon='🤖',
        bot_status='Online 24/7',
        user_icon='👤',
        welcome_title='👋 Olá! Bem-vindo!',
        welcome_text='Como posso ajudar você hoje?',
        suggestions=[
            {'emoji': '💡', 'text': 'O que você pode fazer?'},
            {'emoji': '🎯', 'text': 'Me dê dicas'},
            {'emoji': '📚', 'text': 'Explique algo'}
        ],
        input_placeholder='Digite aqui...',
        send_button_text='Enviar',
        api_endpoint='/api/chat'
    )

def extract_text_from_file(file_storage):
    """
    Detecta extensão e retorna o texto extraído.
    Suporta: .pdf, .docx, .txt e várias extensões de código (.py, .js, .html, .css, .json, .c, .cpp, .java, .sql, .xml, .md)
    """
    filename = file_storage.filename or "arquivo"
    filename_lower = filename.lower()
    ext = os.path.splitext(filename_lower)[1]

    try:
        if ext == '.pdf':
            reader = PdfReader(file_storage)
            pages_text = []
            for page in reader.pages:
                try:
                    pages_text.append(page.extract_text() or "")
                except Exception:
                    pages_text.append("")
            return "\n".join(pages_text), filename
        elif ext == '.docx':
            # docx2txt aceita file path ou file-like; file_storage is file-like, but to be safe we read bytes
            tmp_bytes = file_storage.read()
            # docx2txt expects a path or file; easiest: save to a temporary file
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tf:
                tf.write(tmp_bytes)
                tmp_path = tf.name
            text = docx2txt.process(tmp_path)
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            return text or "", filename
        elif ext in ['.txt', '.py', '.js', '.html', '.css', '.json', '.c', '.cpp', '.java', '.sql', '.xml', '.md', '.yml', '.yaml', '.ts']:
            # texto simples / código
            raw = file_storage.read()
            try:
                return raw.decode('utf-8', errors='ignore'), filename
            except Exception:
                return str(raw), filename
        else:
            return None, filename  # formato não suportado
    except Exception as e:
        # Retorna erro para o chamador
        raise e

def parse_dify_answer(response_json):
    """
    Extrai a melhor propriedade provável (answer, choices, output, etc).
    """
    # Possíveis formatos:
    # { "answer": "..." }
    # { "output": "..."}
    # { "choices": [ { "text": "..." }, ... ] }
    # { "results": [ { "message": { "content": "..." } } ] }
    if not response_json:
        return ""

    if isinstance(response_json, dict):
        if 'answer' in response_json:
            return response_json.get('answer') or ""
        if 'output' in response_json:
            return response_json.get('output') or ""
        if 'choices' in response_json and isinstance(response_json['choices'], list) and len(response_json['choices']) > 0:
            first = response_json['choices'][0]
            return first.get('text', '') if isinstance(first, dict) else str(first)
        if 'results' in response_json and isinstance(response_json['results'], list) and len(response_json['results']) > 0:
            # tentativa genérica
            first = response_json['results'][0]
            if isinstance(first, dict):
                # várias estruturas possíveis
                if 'message' in first:
                    msg = first.get('message')
                    if isinstance(msg, dict):
                        # procurar content
                        return msg.get('content', '') or ""
            return str(first)
        # fallback: stringify
        # sometimes dify returns messages in other nested fields
        # try to get top-level text-like fields
        for k in ['message', 'text', 'content']:
            if k in response_json:
                return response_json.get(k) or ""
    # fallback
    return str(response_json)

@app.route('/api/chat', methods=['POST'])
def chat():
    try:
        # Aceita multipart/form-data (form + arquivo)
        user_message = request.form.get('message', '') or ''
        conversation_id = request.form.get('conversation_id', '') or ''
        file = request.files.get('file')

        file_text = ''
        filename = ''
        if file:
            extracted, filename = extract_text_from_file(file)
            if extracted is None:
                return jsonify({'success': False, 'error': f'Formato não suportado para o arquivo {filename}. Use PDF, DOCX, TXT ou arquivos de código.'}), 400
            file_text = extracted

        # Monta o prompt/contexto que será enviado ao Dify
        query_parts = []
        if user_message.strip():
            query_parts.append(user_message.strip())

        if file_text and file_text.strip():
            # sinaliza que é código se extensão for de código
            _, ext = os.path.splitext(filename.lower())
            if ext in ['.py', '.js', '.html', '.css', '.json', '.c', '.cpp', '.java', '.sql', '.xml', '.md', '.ts', '.yml', '.yaml']:
                header = f"📄 Arquivo de código: {filename}\nExplique, debug ou responda conforme a solicitação do usuário.\n\n"
            else:
                header = f"📄 Conteúdo do arquivo: {filename}\n\n"
            # limite para evitar estouro — ajuste conforme necessidade
            max_chars = 14000  # permite mais texto antes de truncar (ajuste conforme orçamento/tokens)
            file_text_trimmed = file_text[:max_chars]
            query_parts.append(header + file_text_trimmed)

        if not query_parts:
            return jsonify({'success': False, 'error': 'Mensagem e/ou conteúdo do arquivo ausentes.'}), 400

        full_query = "\n\n".join(query_parts)

        payload = {
            "inputs": {},
            "query": full_query,
            "response_mode": "blocking",
            "user": "user-" + (request.remote_addr or "unknown"),
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id

        headers = {
            "Authorization": f"Bearer {DIFY_API_KEY}",
            "Content-Type": "application/json"
        }

        # Log de envio
        print("=== Enviando para Dify ===")
        print("Payload (trecho):", full_query[:1200].replace("\n", "\\n"))
        print("==========================")

        response = requests.post(DIFY_API_URL, json=payload, headers=headers, timeout=120)

        # Log da resposta
        print("=== Resposta Dify ===")
        print("Status Code:", response.status_code)
        print("Resposta bruta:", response.text)
        print("=====================")

        try:
            dify_data = response.json()
        except Exception:
            dify_data = None

        if response.status_code == 200:
            answer = parse_dify_answer(dify_data)
            return jsonify({
                'success': True,
                'message': answer or 'Sem resposta textual da API.',
                'conversation_id': (dify_data.get('conversation_id') if isinstance(dify_data, dict) else ''),
                'raw': dify_data
            })
        else:
            # retorna texto de erro (útil para debug)
            return jsonify({'success': False, 'error': f'Erro Dify ({response.status_code}): {response.text}'}), response.status_code

    except Exception as e:
        traceback.print_exc()
        return jsonify({'success': False, 'error': f'Erro interno no servidor: {str(e)}'}), 500

# rota favicon para evitar 404 no console
@app.route('/favicon.ico')
def favicon():
    return '', 204

if __name__ == '__main__':
    # debug True para dev; em produção desligue
    app.run(debug=True, host='0.0.0.0', port=5000)
