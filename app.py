import logging
import os
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
from werkzeug.exceptions import HTTPException

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

STYLES = [
    "официальный",
    "ироничный",
    "молодежный",
    "вдохновляющий",
    "дружеский",
    "провокационный",
    "экспертный",
    "эмоциональный",
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

FETCH_TIMEOUT = 10
LLM_TIMEOUT = 30
MIN_CONTENT_LENGTH = 100
MAX_CONTENT_LENGTH = 3000

URL_PATTERN = re.compile(r"^https?://", re.IGNORECASE)

DEFAULT_LLM_ENDPOINT = "https://api.openai.com/v1/chat/completions"
DEFAULT_TEMPERATURE = 0.8
MIN_TEMPERATURE = 0.0
MAX_TEMPERATURE = 2.0

BASE_DIR = Path(__file__).resolve().parent
SPROMPT_FILE = BASE_DIR / "sprompt.md"
STYLE_FILE = BASE_DIR / "Style.md"


def get_api_key():
    return os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")


def get_proxies():
    http_proxy = os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
    https_proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
    if not http_proxy and not https_proxy:
        return None
    return {
        "http": http_proxy or https_proxy,
        "https": https_proxy or http_proxy,
    }


@app.route("/")
def index():
    return render_template("index.html", styles=STYLES)


@app.route("/generate", methods=["POST"])
def generate():
    validated, error_response = validate_request(request.get_json(silent=True))
    if error_response:
        return error_response

    url = validated["url"]
    style = validated["style"]
    temperature = validated["temperature"]

    try:
        page_content = fetch_page_content(url)
    except PageFetchError as exc:
        return jsonify({"error": str(exc), "error_type": exc.error_type}), exc.status_code
    except Exception as exc:
        logger.exception("Unexpected error while fetching URL: %s", url)
        return jsonify({
            "error": f"Ошибка обработки страницы: {exc}",
            "error_type": "parse",
        }), 500

    try:
        post_text = generate_post_with_llm(
            build_system_prompt(style, page_content),
            style,
            temperature=temperature,
        )
    except LlmError as exc:
        return jsonify({"error": str(exc), "error_type": exc.error_type}), exc.status_code
    except Exception as exc:
        logger.exception("Unexpected error while generating post for URL: %s", url)
        return jsonify({
            "error": f"Неизвестная ошибка: {exc}. Попробуйте позже.",
            "error_type": "unknown",
        }), 500

    return jsonify({
        "post": post_text,
        "style": style,
    })


@app.errorhandler(404)
def not_found(_error):
    if request.path == "/generate":
        return jsonify({
            "error": f"Эндпоинт не найден: {request.path}. Перезапустите сервер Flask.",
        }), 404
    return _error


@app.errorhandler(500)
def internal_error(error):
    logger.exception("Internal server error: %s", error)
    return jsonify({
        "error": f"Внутренняя ошибка сервера: {error}. Перезапустите сервер Flask.",
    }), 500


@app.errorhandler(Exception)
def handle_unexpected_error(error):
    if isinstance(error, HTTPException):
        return error
    if request.path == "/generate":
        logger.exception("Unhandled API error on %s: %s", request.path, error)
        return jsonify({
            "error": f"Неизвестная ошибка: {error}. Попробуйте позже.",
            "error_type": "unknown",
        }), 500
    raise error


def validate_request(data):
    if not data:
        return None, (jsonify({"error": "Некорректный JSON в запросе."}), 400)

    url = (data.get("url") or "").strip()
    style = (data.get("style") or "").strip()

    if not url:
        return None, (jsonify({"error": "Поле URL не может быть пустым."}), 400)
    if not URL_PATTERN.match(url):
        return None, (jsonify({"error": "URL должен начинаться с http:// или https://."}), 400)
    if not style:
        return None, (jsonify({"error": "Выберите стиль поста."}), 400)
    if style not in STYLES:
        return None, (jsonify({"error": "Выбран недопустимый стиль поста."}), 400)

    temperature, temp_error = parse_temperature(data)
    if temp_error:
        return None, temp_error

    return {"url": url, "style": style, "temperature": temperature}, None


def parse_temperature(data):
    raw = data.get("temperature", DEFAULT_TEMPERATURE)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, (jsonify({
            "error": "Некорректное значение креативности (temperature).",
        }), 400)

    if not MIN_TEMPERATURE <= value <= MAX_TEMPERATURE:
        return None, (jsonify({
            "error": f"Креативность должна быть от {MIN_TEMPERATURE:g} до {MAX_TEMPERATURE:g}.",
        }), 400)

    return round(value, 2), None


class PageFetchError(Exception):
    def __init__(self, message, error_type="page", status_code=400):
        super().__init__(message)
        self.error_type = error_type
        self.status_code = status_code


class LlmError(Exception):
    def __init__(self, message, error_type="llm", status_code=502):
        super().__init__(message)
        self.error_type = error_type
        self.status_code = status_code


def fetch_page_content(url):
    proxies = get_proxies()
    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=FETCH_TIMEOUT,
            allow_redirects=True,
            proxies=proxies,
        )
    except requests.exceptions.Timeout:
        logger.error("Timeout fetching URL: %s", url)
        raise PageFetchError(
            "Ошибка: Превышено время ожидания ответа от сайта. "
            "Проверьте URL или попробуйте позже.",
            error_type="timeout",
            status_code=408,
        )
    except requests.exceptions.ConnectionError as exc:
        logger.error("Connection error for URL %s: %s", url, exc)
        raise PageFetchError(
            "Ошибка соединения: Не удалось подключиться к сайту. Проверьте URL.",
            error_type="connection",
            status_code=502,
        )
    except requests.exceptions.RequestException as exc:
        logger.error("Request error for URL %s: %s", url, exc)
        raise PageFetchError(
            f"Неизвестная ошибка: {exc}. Попробуйте позже.",
            error_type="unknown",
            status_code=500,
        )

    status = response.status_code
    if status == 403:
        raise PageFetchError(
            "Ошибка 403: Доступ к странице запрещён. "
            "Сайт блокирует автоматический сбор данных.",
            error_type="http_403",
            status_code=403,
        )
    if status == 404:
        raise PageFetchError(
            "Ошибка 404: Страница не найдена. Проверьте правильность URL.",
            error_type="http_404",
            status_code=404,
        )
    if 400 <= status < 500:
        raise PageFetchError(
            f"Ошибка {status}: Сервер отклонил запрос. "
            "Возможно, страница недоступна.",
            error_type="http_4xx",
            status_code=status,
        )
    if 500 <= status < 600:
        raise PageFetchError(
            f"Ошибка {status}: Проблема на стороне сайта. Попробуйте позже.",
            error_type="http_5xx",
            status_code=status,
        )

    response.encoding = response.apparent_encoding or response.encoding or "utf-8"

    try:
        content = extract_text_from_html(response.text)
    except Exception as exc:
        logger.exception("HTML parse error for URL %s: %s", url, exc)
        raise PageFetchError(
            f"Ошибка разбора страницы: {exc}. Попробуйте другой URL.",
            error_type="parse",
            status_code=422,
        )
    if len(content) < MIN_CONTENT_LENGTH:
        raise PageFetchError(
            "Ошибка: На странице недостаточно текстового контента для генерации поста.",
            error_type="insufficient_content",
            status_code=422,
        )

    return content


def extract_text_from_html(html):
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "svg", "iframe", "form"]):
        tag.decompose()

    parts = []

    title = soup.find("title")
    if title and title.get_text(strip=True):
        parts.append(f"Заголовок: {title.get_text(strip=True)}")

    for meta_attrs in (
        {"name": re.compile(r"^description$", re.I)},
        {"property": re.compile(r"^og:description$", re.I)},
        {"name": re.compile(r"^twitter:description$", re.I)},
    ):
        meta_tag = soup.find("meta", attrs=meta_attrs)
        if meta_tag and meta_tag.get("content", "").strip():
            parts.append(f"Описание: {meta_tag['content'].strip()}")
            break

    body_text = _extract_primary_content(soup)
    fallback_text = _extract_body_fallback(soup)
    body_text = max((body_text, fallback_text), key=len)

    if body_text:
        parts.append(body_text)

    full_text = " ".join(parts)
    full_text = re.sub(r"\s+", " ", full_text).strip()
    return full_text[:MAX_CONTENT_LENGTH]


def _extract_primary_content(soup):
    for tag_name in ("article", "main"):
        element = soup.find(tag_name)
        if element:
            text = element.get_text(separator=" ", strip=True)
            if text:
                return text

    role_main = soup.find(attrs={"role": re.compile(r"^main$", re.I)})
    if role_main:
        text = role_main.get_text(separator=" ", strip=True)
        if text:
            return text

    paragraphs = [p.get_text(strip=True) for p in soup.find_all("p") if p.get_text(strip=True)]
    if paragraphs:
        text = " ".join(paragraphs)
        if text:
            return text

    blocks = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote"]):
        text = tag.get_text(strip=True)
        if text and len(text) > 2:
            blocks.append(text)

    return " ".join(blocks)


def _extract_body_fallback(soup):
    body = soup.find("body")
    if not body:
        return ""
    return body.get_text(separator=" ", strip=True)


def load_system_prompt_template():
    if not SPROMPT_FILE.is_file():
        logger.error("System prompt file not found: %s", SPROMPT_FILE)
        raise LlmError(
            "Ошибка генерации: файл системного промпта sprompt.md не найден.",
            error_type="prompt_config",
            status_code=500,
        )
    return SPROMPT_FILE.read_text(encoding="utf-8-sig")


def get_style_description(style):
    if not STYLE_FILE.is_file():
        return f"Стиль «{style}»: адаптируй тон, лексику и подачу под название стиля."

    content = STYLE_FILE.read_text(encoding="utf-8-sig")
    title = style[0].upper() + style[1:] if style else ""

    for section in content.split("## "):
        section = section.strip()
        if not section:
            continue
        heading, _, body = section.partition("\n")
        if heading.strip().lower() == title.lower():
            return body.strip()

    return f"Стиль «{style}»: адаптируй тон, лексику и подачу под название стиля."


PROMPT_PLACEHOLDERS = ("стиль", "описание_стиля", "текст_с_сайта")


def substitute_prompt_placeholders(template, style, page_content):
    style_description = get_style_description(style)
    replacements = {
        "стиль": style,
        "описание_стиля": style_description,
        "текст_с_сайта": page_content,
    }

    result = template
    for key in PROMPT_PLACEHOLDERS:
        value = replacements[key]
        pattern = re.compile(r"\{\s*" + re.escape(key) + r"\s*\}")
        result = pattern.sub(lambda _match, val=value: val, result)

    remaining = re.findall(r"\{([а-яёa-z_]+)\}", result, flags=re.IGNORECASE)
    if remaining:
        logger.warning("Unsubstituted prompt placeholders: %s", remaining)

    return result


def build_system_prompt(style, page_content):
    template = load_system_prompt_template()
    return substitute_prompt_placeholders(template, style, page_content)


def build_user_prompt(style):
    return (
        f"Напиши пост для социальных сетей в стиле «{style}» на основе текста выше. "
        "Верни только готовый текст поста — без заголовков, без разделов, "
        "без повторения инструкций, без служебных меток и БЕЗ ХЕШТЕГОВ (символ # запрещён)."
    )


POST_MAX_LENGTH = 800

HASHTAG_PATTERN = re.compile(r"#[\w\u0400-\u04FF_-]+", re.UNICODE)

LEAK_MARKERS = (
    "════ USER ════",
    "════ SYSTEM ════",
    "════ ПАРАМЕТРЫ ════",
    "════ ТЕКСТ С САЙТА ════",
    "# ОТВЕТ",
    "# РОЛЬ",
    "# ЗАДАЧА",
    "# ЖЁСТКИЕ ОГРАНИЧЕНИЯ",
)

LEAK_LINE_PATTERNS = (
    re.compile(r"^#+\s", re.IGNORECASE),
    re.compile(r"^════"),
    re.compile(r"^Напиши пост", re.IGNORECASE),
    re.compile(r"^Только текст поста", re.IGNORECASE),
    re.compile(r"^соблюдая все ограничения", re.IGNORECASE),
    re.compile(r"^ничего больше\.?$", re.IGNORECASE),
    re.compile(r"^SYSTEM\s*═", re.IGNORECASE),
    re.compile(r"^USER\s*═", re.IGNORECASE),
)


def remove_hashtags(text):
    cleaned, removed = HASHTAG_PATTERN.subn("", text)
    if removed:
        logger.info("Removed %s hashtag(s) from LLM post", removed)
    lines = []
    for line in cleaned.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines).strip()


def clean_llm_post(text):
    cleaned = text.strip()

    for marker in LEAK_MARKERS:
        if marker in cleaned:
            cleaned = cleaned.split(marker)[0].strip()

    lines = []
    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if any(pattern.search(stripped) for pattern in LEAK_LINE_PATTERNS):
            continue
        lines.append(stripped)

    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()

    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in "\"«»'":
        cleaned = cleaned[1:-1].strip()

    return remove_hashtags(cleaned)


def enforce_post_length(text, max_length=POST_MAX_LENGTH):
    if len(text) <= max_length:
        return text
    truncated = text[:max_length]
    last_space = truncated.rfind(" ")
    if last_space > max_length * 0.6:
        truncated = truncated[:last_space]
    return truncated.rstrip(".,;:!? ") + "…"


def generate_post_with_llm(system_prompt, style, temperature=DEFAULT_TEMPERATURE):
    endpoint = os.getenv("LLM_API_ENDPOINT", DEFAULT_LLM_ENDPOINT)
    api_key = get_api_key()
    model = os.getenv("LLM_MODEL", "gpt-3.5-turbo")
    proxies = get_proxies()

    if not api_key:
        logger.error("LLM_API_KEY or OPENAI_API_KEY is not configured in .env")
        raise LlmError(
            "Ошибка генерации: Сервис генерации не настроен. Обратитесь к администратору.",
            error_type="llm_config",
            status_code=500,
        )

    prompt = system_prompt

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": build_user_prompt(style)},
        ],
        "temperature": temperature,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            endpoint,
            json=payload,
            headers=headers,
            timeout=LLM_TIMEOUT,
            proxies=proxies,
        )
    except requests.exceptions.Timeout:
        logger.error("LLM API timeout")
        raise LlmError(
            "Ошибка: Сервис генерации не отвечает. Попробуйте позже.",
            error_type="llm_timeout",
            status_code=504,
        )
    except requests.exceptions.RequestException as exc:
        logger.error("LLM API connection error: %s", exc)
        raise LlmError(
            f"Неизвестная ошибка: {exc}. Попробуйте позже.",
            error_type="unknown",
            status_code=500,
        )

    if response.status_code != 200:
        error_text = _extract_api_error(response)
        logger.error("LLM API error %s: %s", response.status_code, error_text)
        raise LlmError(
            f"Ошибка генерации: {error_text}. Попробуйте другой стиль или страницу.",
            error_type="llm_api",
            status_code=502,
        )

    try:
        result = response.json()
    except ValueError as exc:
        logger.error("Invalid LLM API JSON: %s", exc)
        raise LlmError(
            "Ошибка: Сервис вернул некорректный ответ. Попробуйте позже.",
            error_type="llm_parse",
            status_code=502,
        )

    content = (
        result.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )

    content = clean_llm_post(content)
    content = enforce_post_length(content)

    if not content:
        logger.error("Empty content from LLM API")
        raise LlmError(
            "Ошибка: Сервис вернул пустой ответ. Попробуйте другой стиль или страницу.",
            error_type="llm_empty",
            status_code=502,
        )

    return content


def _extract_api_error(response):
    try:
        data = response.json()
        if isinstance(data, dict):
            if "error" in data:
                err = data["error"]
                if isinstance(err, dict):
                    return err.get("message", str(err))
                return str(err)
            return data.get("message", response.text[:200])
    except ValueError:
        pass
    return response.text[:200] or f"HTTP {response.status_code}"


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
