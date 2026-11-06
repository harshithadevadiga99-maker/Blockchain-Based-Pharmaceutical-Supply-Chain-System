import os
import uuid
from gtts import gTTS
from deep_translator import GoogleTranslator

LANGUAGES = {
    'en': {'name': 'English', 'code': 'en', 'gtts': 'en', 'flag': '🇬🇧'},
    'hi': {'name': 'हिन्दी (Hindi)', 'code': 'hi', 'gtts': 'hi', 'flag': '🇮🇳'},
    'kn': {'name': 'ಕನ್ನಡ (Kannada)', 'code': 'kn', 'gtts': 'kn', 'flag': '🇮🇳'},
    'te': {'name': 'తెలుగు (Telugu)', 'code': 'te', 'gtts': 'te', 'flag': '🇮🇳'},
    'ta': {'name': 'தமிழ் (Tamil)', 'code': 'ta', 'gtts': 'ta', 'flag': '🇮🇳'},
    'ml': {'name': 'മലയാളം (Malayalam)', 'code': 'ml', 'gtts': 'ml', 'flag': '🇮🇳'},
}

AUDIO_DIR = 'static/audio'
os.makedirs(AUDIO_DIR, exist_ok=True)


def translate_text(text, target_lang='en'):
    if target_lang == 'en' or not text:
        return text
    try:
        translator = GoogleTranslator(source='auto', target=target_lang)
        if len(text) > 4500:
            chunks = [text[i:i+4500] for i in range(0, len(text), 4500)]
            return ' '.join(translator.translate(c) for c in chunks)
        return translator.translate(text)
    except Exception as e:
        print(f"Translation error: {e}")
        return text


def text_to_speech(text, lang='en'):
    try:
        filename = f"tts_{uuid.uuid4().hex}.mp3"
        filepath = os.path.join(AUDIO_DIR, filename)
        tts = gTTS(text=text, lang=LANGUAGES.get(lang, {}).get('gtts', 'en'), slow=False)
        tts.save(filepath)
        return f'/static/audio/{filename}'
    except Exception as e:
        print(f"TTS error: {e}")
        return None


def cleanup_audio(max_files=50):
    try:
        files = sorted(
            [os.path.join(AUDIO_DIR, f) for f in os.listdir(AUDIO_DIR)],
            key=os.path.getmtime
        )
        for f in files[:-max_files]:
            os.remove(f)
    except Exception:
        pass