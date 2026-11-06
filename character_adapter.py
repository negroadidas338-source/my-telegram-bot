"""Пользовательские персонажи Telegram Mini App: UI, API и отдельное хранилище."""

import asyncio
import hashlib
import hmac
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qsl

from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ChatType
from aiogram.filters import Command


APP_HTML = r'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no"><meta name="theme-color" content="#7657ed"><title>Персонажи</title>
<script src="https://telegram.org/js/telegram-web-app.js?64"></script>
<style>
:root{--purple:#7657ed;--ink:#25243a;--muted:#82839b;--line:#ecebf3;--soft:#f7f7fb;--lav:#f2efff}*{box-sizing:border-box}body{margin:0;background:#f5f4fb;color:var(--ink);font:15px -apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif}button,input,textarea{font:inherit}button{cursor:pointer}#app{max-width:520px;min-height:100dvh;margin:auto;background:#fff;position:relative;padding-bottom:78px;box-shadow:0 0 36px #3025680c}.top{padding:22px 20px 14px;background:white;position:sticky;top:0;z-index:3;border-bottom:1px solid #f0eff5}.eyebrow{font-size:11px;letter-spacing:.12em;color:var(--purple);font-weight:800}.top h1{margin:7px 0 4px;font-size:24px}.sub{font-size:13px;color:var(--muted)}main{padding:16px 20px 28px}.search,.field{width:100%;border:1px solid #e8e7f0;background:var(--soft);border-radius:13px;padding:13px 14px;color:var(--ink);outline:none}.search:focus,.field:focus{border-color:#bcb0ff}.section-title{font-size:16px;font-weight:750;margin:22px 0 12px}.card{border:1px solid var(--line);border-radius:17px;padding:14px;display:flex;gap:12px;align-items:center;margin:10px 0;background:white;cursor:pointer}.avatar{width:54px;height:54px;border-radius:50%;background:linear-gradient(135deg,#f9b78e,#dd6c79);display:grid;place-items:center;color:white;font-size:20px;font-weight:750;flex:none;overflow:hidden}.avatar img{width:100%;height:100%;object-fit:cover}.card-info{min-width:0;flex:1}.card-name{font-size:14px;font-weight:750;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.card-desc{font-size:12px;color:var(--muted);margin-top:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.tag{display:inline-block;font-size:9px;font-weight:800;letter-spacing:.04em;background:var(--lav);color:var(--purple);border-radius:20px;padding:5px 8px;margin-top:8px}.primary{border:0;border-radius:15px;background:var(--purple);color:white;font-weight:750;padding:16px;width:100%;box-shadow:0 5px 14px #7657ed24}.primary:disabled{opacity:.55}.outline{border:1px solid #ded9f5;background:white;color:var(--purple);border-radius:12px;padding:10px 12px;font-weight:650}.bottom{position:fixed;z-index:4;bottom:0;left:50%;transform:translateX(-50%);width:min(520px,100%);height:66px;background:#ffffffed;backdrop-filter:blur(14px);border-top:1px solid var(--line);display:flex;justify-content:space-around;align-items:center}.nav{border:0;background:none;color:var(--muted);font-size:12px;padding:10px}.nav.active{color:var(--purple);font-weight:750}.label{display:block;font-size:11px;font-weight:800;letter-spacing:.045em;margin:17px 0 7px}.field{font-size:14px}.fieldarea{min-height:96px;resize:vertical;line-height:1.45}.photo-row{display:flex;align-items:center;gap:14px;margin:10px 0 18px}.photo-add{border:1px dashed #b9aff4;background:#f2efff;color:var(--purple);border-radius:50%;width:64px;height:64px;font-size:25px}.checkbox{display:flex;align-items:center;gap:9px;font-size:13px;margin:17px 0}.checkbox input{accent-color:var(--purple);width:18px;height:18px}.back{color:var(--purple);font-weight:700;border:0;background:none;padding:4px 0 12px}.chat-head{display:flex;align-items:center;gap:12px;padding:12px 20px;border-bottom:1px solid var(--line);position:sticky;top:0;background:white;z-index:2}.chat-head .avatar{width:42px;height:42px}.online{font-size:11px;color:#6ea88b;margin-top:3px}.chat-log{padding:18px 16px 96px;display:flex;flex-direction:column;gap:12px;min-height:65vh;background:#fff}.bubble{max-width:84%;padding:12px 14px;border-radius:17px;background:#f4f2fa;line-height:1.45;white-space:pre-wrap;overflow-wrap:anywhere}.bubble.user{align-self:flex-end;background:var(--purple);color:white}.bubble.assistant{align-self:flex-start}.chat-form{position:fixed;bottom:76px;left:50%;transform:translateX(-50%);width:min(488px,calc(100% - 28px));display:flex;gap:8px;background:#f7f7fb;border:1px solid #e8e7f0;border-radius:22px;padding:7px}.chat-form input{border:0;background:transparent;outline:0;min-width:0;flex:1;padding:8px}.send{width:40px;height:40px;border:0;border-radius:50%;background:var(--purple);color:white;font-size:18px}.empty{text-align:center;padding:42px 16px;color:var(--muted);line-height:1.5}.error{margin:12px 0;padding:11px 13px;color:#a23d53;background:#fff0f2;border-radius:12px;font-size:13px}.spinner{opacity:.65;text-align:center;padding:20px}
</style></head><body><div id="app"></div><script>
const tg=window.Telegram&&window.Telegram.WebApp; if(tg){tg.ready();tg.expand();try{tg.setHeaderColor('#ffffff');tg.setBackgroundColor('#f5f4fb')}catch(e){}}
const initData=tg?tg.initData:'';const root=document.getElementById('app');let page='catalog',items=[],myChats=[],active=null,history=[],chatMemory='',busy=false;
function esc(s){return String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function avatar(c){return c.avatar?`<div class="avatar"><img src="${esc(c.avatar)}" alt=""></div>`:`<div class="avatar">${esc((c.name||'?').slice(0,1).toUpperCase())}</div>`}
async function api(path,method='GET',body){const r=await fetch(path,{method,headers:{'Content-Type':'application/json','X-Telegram-Init-Data':initData},body:body?JSON.stringify(body):undefined});const d=await r.json().catch(()=>({error:'Ошибка ответа сервера'}));if(!r.ok)throw Error(d.error||'Не удалось выполнить запрос');return d}
function header(title,sub=''){return `<header class="top"><div class="eyebrow">ТВОЙ МИР ПЕРСОНАЖЕЙ</div><h1>${title}</h1>${sub?`<div class="sub">${sub}</div>`:''}</header>`}
function nav(){return `<nav class="bottom"><button class="nav ${page==='catalog'?'active':''}" onclick="go('catalog')">Каталог</button><button class="nav ${page==='chats'?'active':''}" onclick="go('chats')">Мои чаты</button><button class="nav ${page==='profile'?'active':''}" onclick="go('profile')">Профиль</button></nav>`}
function card(c){return `<article class="card" onclick="openChat('${esc(c.id)}')">${avatar(c)}<div class="card-info"><div class="card-name">${esc(c.name)}</div><div class="card-desc">${esc(c.description)}</div><span class="tag">${c.is_public?'ПЕРСОНАЖ':'МОЙ ПЕРСОНАЖ'}</span></div><span style="color:#9999ad;font-size:12px">♡ ${Number(c.chat_count||0)}</span></article>`}
function showError(e){const old=document.querySelector('.error');if(old)old.remove();const el=document.createElement('div');el.className='error';el.textContent=e.message||'Что-то пошло не так';root.prepend(el)}
async function go(to){page=to;active=null;render();try{if(to==='catalog'){items=(await api('/api/catalog')).characters;render()}else if(to==='chats'){myChats=(await api('/api/chats')).chats;render()}else{items=(await api('/api/mine')).characters;render()}}catch(e){showError(e)}}
function render(){if(page==='create'){renderCreate();return}if(page==='chat'&&active){renderChat();return}if(page==='catalog'){root.innerHTML=header('Мир персонажей','Выбери собеседника или создай своего')+`<main><input id="q" class="search" placeholder="⌕  Найти персонажа" oninput="filterCards()"><div class="section-title">Популярное</div><div id="cards">${items.map(card).join('')||'<div class="empty">Пока персонажей нет.<br>Создай первого!</div>'}</div><button class="primary" onclick="page='create';render()">Создать персонажа</button></main>`+nav()}else if(page==='chats'){root.innerHTML=header('Мои чаты','Продолжи разговор с персонажем')+`<main>${myChats.map(c=>card(c)).join('')||'<div class="empty">Здесь появятся персонажи,<br>с которыми ты начал общаться.</div>'}</main>`+nav()}else{root.innerHTML=header('Профиль','Твои персонажи')+`<main><div class="section-title">Созданные тобой</div>${items.map(c=>`<article class="card">${avatar(c)}<div class="card-info"><div class="card-name">${esc(c.name)}</div><div class="card-desc">${esc(c.description)}</div><span class="tag">${c.is_public?'В КАТАЛОГЕ':'СКРЫТЫЙ'}</span></div><button class="outline" onclick="togglePublic('${esc(c.id)}',${!c.is_public})">${c.is_public?'Скрыть':'Показать'}</button></article>`).join('')||'<div class="empty">Ты ещё не создал персонажей.</div>'}<button class="primary" onclick="page='create';render()">Создать персонажа</button></main>`+nav()}}
function filterCards(){const q=document.getElementById('q').value.toLowerCase();document.getElementById('cards').innerHTML=items.filter(c=>(c.name+' '+c.description).toLowerCase().includes(q)).map(card).join('')||'<div class="empty">Ничего не найдено</div>'}
function renderCreate(){root.innerHTML=`<main><button class="back" onclick="go('catalog')">‹  Назад</button><h1 style="font-size:24px;margin:0 0 5px">Создать персонажа</h1><div class="sub">Опиши его — и начни общение</div><form id="createForm"><div class="photo-row"><button type="button" class="photo-add" onclick="document.getElementById('photo').click()">＋</button><div><b>Фото персонажа</b><div class="sub" style="margin-top:5px">Необязательно · нажми, чтобы добавить</div></div><input id="photo" type="file" accept="image/png,image/jpeg,image/webp" hidden></div><label class="label">Имя персонажа *</label><input class="field" name="name" maxlength="32" required placeholder="Например, Лея"><label class="label">Короткое описание</label><textarea class="field fieldarea" name="description" maxlength="120" style="min-height:66px" placeholder="Кто это и чем интересен?"></textarea><label class="label">Характер и правила общения</label><textarea class="field fieldarea" name="definition" maxlength="2000" required placeholder="Опиши личность, манеру общения и важные детали..."></textarea><label class="label">Первое сообщение</label><textarea class="field fieldarea" name="greeting" maxlength="400" placeholder="С чего персонаж начнёт разговор?"></textarea><label class="checkbox"><input type="checkbox" name="is_public" checked>Показывать персонажа в общем каталоге</label><button class="primary" id="createBtn">Создать персонажа</button></form></main>`;document.getElementById('photo').onchange=previewPhoto;document.getElementById('createForm').onsubmit=createCharacter}
let photoData='';function previewPhoto(e){const f=e.target.files[0];if(!f)return;if(f.size>260000){showError(Error('Фото должно быть меньше 250 КБ'));e.target.value='';return}const r=new FileReader();r.onload=()=>{photoData=r.result;document.querySelector('.photo-add').style.background=`center/cover no-repeat url('${photoData}')`;document.querySelector('.photo-add').textContent=''};r.readAsDataURL(f)}
async function createCharacter(e){e.preventDefault();const f=e.currentTarget;const b=document.getElementById('createBtn');b.disabled=true;try{const fd=new FormData(f);const c=await api('/api/create','POST',{name:fd.get('name'),description:fd.get('description'),definition:fd.get('definition'),greeting:fd.get('greeting'),is_public:fd.has('is_public'),avatar:photoData});active=c.character;history=[{role:'assistant',content:active.greeting}];chatMemory='';page='chat';renderChat()}catch(err){showError(err);b.disabled=false}}
async function togglePublic(id,value){try{await api('/api/character/'+encodeURIComponent(id),'PATCH',{is_public:value});go('profile')}catch(e){showError(e)}}
async function openChat(id){try{const d=await api('/api/chat/'+encodeURIComponent(id));active=d.character;history=d.messages;chatMemory=d.memory||'';page='chat';renderChat();if(!history.length&&active.greeting){history=[{role:'assistant',content:active.greeting}];addBubble('assistant',active.greeting)}}catch(e){showError(e)}}
function renderChat(){root.innerHTML=`<div class="chat-head"><button class="back" style="padding:8px" onclick="go('catalog')">‹</button>${avatar(active)}<div style="min-width:0;flex:1"><b>${esc(active.name)}</b><div class="online">● созданный персонаж</div></div><button class="outline" style="padding:9px 10px;white-space:nowrap" onclick="editMemory()">🧠 Память</button></div><section class="chat-log" id="log">${history.map(m=>`<div class="bubble ${m.role==='user'?'user':'assistant'}">${esc(m.content)}</div>`).join('')}</section><form class="chat-form" id="chatForm"><input id="message" maxlength="4000" placeholder="Написать сообщение…" autocomplete="off"><button class="send" id="send" aria-label="Отправить">➜</button></form>`;document.getElementById('chatForm').onsubmit=sendMessage;scrollLog()}
function editMemory(){root.innerHTML=`<main><button class="back" onclick="renderChat()">‹  Назад в чат</button><h1 style="font-size:24px;margin:4px 0 5px">Память персонажа</h1><div class="sub">Запиши важные факты, события и предпочтения. Персонаж будет учитывать их в этом чате.</div><form id="memoryForm"><textarea id="memoryText" class="field fieldarea" maxlength="1200" style="min-height:190px;margin-top:18px" placeholder="Например: меня зовут Саша; мы познакомились в университете; я не люблю, когда меня называют Сашенькой.">${esc(chatMemory)}</textarea><div class="sub" style="margin:8px 0 18px">Только для этого чата · до 1200 символов</div><button class="primary" id="saveMemory">Сохранить память</button></form></main>`;document.getElementById('memoryForm').onsubmit=saveMemory}
async function saveMemory(e){e.preventDefault();const button=document.getElementById('saveMemory');button.disabled=true;try{const d=await api('/api/chat/'+encodeURIComponent(active.id)+'/memory','PATCH',{memory:document.getElementById('memoryText').value.trim()});chatMemory=d.memory||'';renderChat()}catch(error){showError(error);button.disabled=false}}
function addBubble(role,text){const log=document.getElementById('log');if(!log)return;const b=document.createElement('div');b.className='bubble '+(role==='user'?'user':'assistant');b.textContent=text;log.appendChild(b);scrollLog()}
function scrollLog(){const l=document.getElementById('log');if(l)l.scrollTop=l.scrollHeight}
async function sendMessage(e){e.preventDefault();if(busy)return;const input=document.getElementById('message');const text=input.value.trim();if(!text)return;input.value='';busy=true;document.getElementById('send').disabled=true;addBubble('user',text);const loading=document.createElement('div');loading.className='spinner';loading.textContent=active.name+' пишет…';document.getElementById('log').appendChild(loading);scrollLog();try{const d=await api('/api/chat/'+encodeURIComponent(active.id),'POST',{text});loading.remove();addBubble('assistant',d.answer);history=d.messages}catch(err){loading.remove();showError(err)}finally{busy=false;const b=document.getElementById('send');if(b)b.disabled=false}}
async function boot(){if(!initData){root.innerHTML=header('Открой из сообщения бота')+'<main><div class="empty">Закрой это окно. В чате бота отправь /characters и нажми кнопку под ответом.<br><br>Кнопка под сообщением передаст Mini App данные для безопасного входа.</div></main>';return}await go('catalog')}
boot();
</script><script src="/feedback-ui.js"></script></body></html>'''


FEEDBACK_UI_JS = r'''(function(){
  const originalRender = window.render;
  let selectedRating = 5;
  window.render = function(){
    if (page === 'feedback') { renderFeedback(); return; }
    originalRender();
    if (page === 'profile') {
      const main = root.querySelector('main');
      if (main && !main.querySelector('#feedbackOpen')) {
        const button = document.createElement('button');
        button.id = 'feedbackOpen';
        button.className = 'primary';
        button.style.margin = '0 0 22px';
        button.textContent = '★  Оставить отзыв';
        button.onclick = function(){ page = 'feedback'; render(); };
        main.prepend(button);
      }
    }
  };
  function renderFeedback(){
    root.innerHTML = `<main><button class="back" onclick="page='profile';render()">‹  Назад в профиль</button><h1 style="font-size:24px;margin:4px 0 5px">Твой отзыв</h1><div class="sub">Помоги сделать бота лучше</div><form id="feedbackForm"><label class="label">Оценка</label><div id="ratingStars" style="display:flex;gap:8px;margin:8px 0 18px">${[1,2,3,4,5].map(n=>`<button type="button" onclick="feedbackRate(${n})" aria-label="${n}">${n<=selectedRating?'★':'☆'}</button>`).join('')}</div><label class="label" for="feedbackText">Комментарий</label><textarea id="feedbackText" class="field fieldarea" maxlength="1500" placeholder="Что понравилось или что стоит улучшить?"></textarea><div class="sub" style="margin:8px 0 18px">Комментарий можно не заполнять</div><button class="primary" id="feedbackSubmit">Отправить отзыв</button></form></main>`;
    document.querySelectorAll('#ratingStars button').forEach(b=>{b.style.cssText='border:0;background:#f2efff;color:#7657ed;border-radius:12px;width:48px;height:48px;font-size:27px';});
    document.getElementById('feedbackForm').onsubmit = submitFeedback;
  }
  window.feedbackRate = function(value){
    selectedRating = Math.max(1, Math.min(5, Number(value)||5));
    document.querySelectorAll('#ratingStars button').forEach((button,index)=>button.textContent=index<selectedRating?'★':'☆');
  };
  async function submitFeedback(event){
    event.preventDefault();
    const button=document.getElementById('feedbackSubmit');
    const message=document.getElementById('feedbackText').value.trim();
    button.disabled=true;
    try{
      await api('/api/feedback','POST',{rating:selectedRating,message});
      root.innerHTML='<main><div class="empty"><div style="font-size:42px;color:#7657ed">✓</div><h1 style="font-size:23px">Спасибо за отзыв!</h1><div class="sub">Он поможет улучшить бота.</div><button class="primary" style="margin-top:24px" onclick="page=\'profile\';render()">Вернуться в профиль</button></div></main>';
    }catch(error){showError(error);button.disabled=false;}
  }
})();'''


class FeedbackStore:
    """Private feedback storage; submissions are never listed in the Mini App."""

    def __init__(self, data_dir: str):
        os.makedirs(data_dir, exist_ok=True)
        self.path = os.path.join(data_dir, "feedback.json")
        self.lock = threading.RLock()

    def add(self, row: dict) -> None:
        with self.lock:
            try:
                with open(self.path, "r", encoding="utf-8") as file:
                    rows = json.load(file)
                if not isinstance(rows, list):
                    rows = []
            except FileNotFoundError:
                rows = []
            tmp_path = self.path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as file:
                json.dump([*rows, row], file, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.path)

    def list_all(self) -> list[dict]:
        with self.lock:
            try:
                with open(self.path, "r", encoding="utf-8") as file:
                    rows = json.load(file)
                return rows if isinstance(rows, list) else []
            except FileNotFoundError:
                return []


# Telegram Mini App launch data may be reused when the app is reopened from a
# cached WebView. Keep signed data usable for a limited period while rejecting
# clearly stale credentials and timestamps far in the future.
INIT_DATA_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
INIT_DATA_FUTURE_SKEW_SECONDS = 5 * 60


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CharacterStore:
    """Small atomic JSON stores; independent from the bot's conversation memory."""

    def __init__(self, data_dir: str):
        os.makedirs(data_dir, exist_ok=True)
        self.characters_path = os.path.join(data_dir, "characters.json")
        self.chats_path = os.path.join(data_dir, "character_chats.json")
        self.lock = threading.RLock()

    def _read(self, path: str, default):
        try:
            with open(path, "r", encoding="utf-8") as file:
                return json.load(file)
        except FileNotFoundError:
            return default

    def _write(self, path: str, data) -> None:
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except OSError:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            raise

    def _public_character(self, character: dict) -> dict:
        return {key: character.get(key) for key in (
            "id", "name", "description", "greeting", "avatar", "is_public", "created_at", "chat_count"
        )}

    def visible_to(self, user_id: int) -> list[dict]:
        with self.lock:
            all_chars = self._read(self.characters_path, [])
            all_chats = self._read(self.chats_path, {})
            result = [c for c in all_chars if c.get("is_public") or c.get("owner_id") == user_id]
            for character in result:
                character["chat_count"] = sum(
                    1 for chat in all_chats.values() if chat.get("character_id") == character.get("id")
                )
            result.sort(key=lambda c: (c.get("chat_count", 0), c.get("created_at", "")), reverse=True)
            return [self._public_character(c) for c in result]

    def mine(self, user_id: int) -> list[dict]:
        with self.lock:
            all_chars = self._read(self.characters_path, [])
            result = [c for c in all_chars if c.get("owner_id") == user_id]
            return [self._public_character(c) for c in sorted(result, key=lambda c: c.get("created_at", ""), reverse=True)]

    def get_character(self, character_id: str, user_id: int) -> dict | None:
        with self.lock:
            for character in self._read(self.characters_path, []):
                if character.get("id") == character_id and (character.get("is_public") or character.get("owner_id") == user_id):
                    return dict(character)
        return None

    def create_character(self, user_id: int, values: dict) -> dict:
        character = {
            "id": uuid.uuid4().hex,
            "owner_id": user_id,
            "name": values["name"],
            "description": values["description"],
            "definition": values["definition"],
            "greeting": values["greeting"],
            "avatar": values["avatar"],
            "is_public": values["is_public"],
            "chat_count": 0,
            "created_at": _utc_now(),
        }
        with self.lock:
            chars = self._read(self.characters_path, [])
            chars.append(character)
            self._write(self.characters_path, chars)
        return self._public_character(character)

    def set_visibility(self, character_id: str, user_id: int, is_public: bool) -> bool:
        with self.lock:
            chars = self._read(self.characters_path, [])
            for character in chars:
                if character.get("id") == character_id and character.get("owner_id") == user_id:
                    character["is_public"] = is_public
                    self._write(self.characters_path, chars)
                    return True
        return False

    def chat_history(self, user_id: int, character_id: str) -> list[dict]:
        key = f"{user_id}:{character_id}"
        with self.lock:
            chats = self._read(self.chats_path, {})
            return list(chats.get(key, {}).get("messages", []))

    def save_chat(self, user_id: int, character_id: str, messages: list[dict]) -> None:
        key = f"{user_id}:{character_id}"
        with self.lock:
            chats = self._read(self.chats_path, {})
            chat = chats.get(key, {})
            chat.update({
                "user_id": user_id,
                "character_id": character_id,
                "messages": messages[-40:],
                "updated_at": _utc_now(),
            })
            chats[key] = chat
            self._write(self.chats_path, chats)

    def chat_memory(self, user_id: int, character_id: str) -> str:
        key = f"{user_id}:{character_id}"
        with self.lock:
            chats = self._read(self.chats_path, {})
            chat = chats.get(key, {})
            return str(chat.get("memory", ""))

    def save_chat_memory(self, user_id: int, character_id: str, memory: str) -> None:
        key = f"{user_id}:{character_id}"
        with self.lock:
            chats = self._read(self.chats_path, {})
            chat = chats.get(key, {})
            chat.update({
                "user_id": user_id,
                "character_id": character_id,
                "memory": memory,
                "updated_at": _utc_now(),
            })
            chats[key] = chat
            self._write(self.chats_path, chats)

    def list_chats(self, user_id: int) -> list[dict]:
        with self.lock:
            chats = self._read(self.chats_path, {})
            chars = self._read(self.characters_path, [])
            by_id = {c.get("id"): c for c in chars}
            rows = []
            for key, chat in chats.items():
                if chat.get("user_id") != user_id:
                    continue
                character = by_id.get(chat.get("character_id"))
                if character and (character.get("is_public") or character.get("owner_id") == user_id):
                    row = self._public_character(character)
                    row["updated_at"] = chat.get("updated_at", "")
                    rows.append(row)
            return sorted(rows, key=lambda c: c.get("updated_at", ""), reverse=True)


class CharacterAdapter:
    """One registration point for the UI, Telegram command, and HTTP API."""

    def __init__(self, bot: Bot, bot_token: str, dispatcher: Dispatcher, data_dir: str,
                 base_url: str, generate_reply):
        self.bot = bot
        self.bot_token = bot_token
        self.dp = dispatcher
        self.base_url = base_url.rstrip("/")
        self.app_url = f"{self.base_url}/app" if self.base_url.startswith("https://") else ""
        self.store = CharacterStore(data_dir)
        self.feedback_store = FeedbackStore(data_dir)
        self.generate_reply = generate_reply
        self.chat_locks: dict[str, asyncio.Lock] = {}

    def keyboard(self):
        if not self.app_url:
            return None
        return types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(
                text="🎭 Открыть каталог персонажей",
                web_app=types.WebAppInfo(url=self.app_url),
            )]],
        )

    def register_commands(self) -> None:
        self.dp.message.register(self.open_app_command, Command("characters"))
        self.dp.message.register(self.show_feedback_command, Command("feedbacks"))
        self.dp.message.register(
            self.show_feedback_command,
            lambda message: (message.text or "").strip().casefold() in {
                "feedbacks", "feedback", "отзывы", "мои отзывы",
            },
        )

    async def show_feedback_command(self, message: types.Message) -> None:
        if message.chat.type != ChatType.PRIVATE:
            await message.answer("Отзывы можно просматривать только в личном чате с ботом.")
            return
        print(f"Feedback review requested in private chat {message.chat.id}.")
        parts = (message.text or "").split()
        try:
            page = max(1, int(parts[1])) if len(parts) > 1 else 1
        except ValueError:
            await message.answer("Используй команду так: /feedbacks или /feedbacks 2")
            return
        rows = self.feedback_store.list_all()
        if not rows:
            await message.answer("Пока отзывов нет.")
            return
        newest_first = list(reversed(rows))
        page_size = 5
        start = (page - 1) * page_size
        selected = newest_first[start:start + page_size]
        page_count = max(1, (len(newest_first) + page_size - 1) // page_size)
        if not selected:
            await message.answer(f"Такой страницы нет. Отправь /feedbacks {page_count}")
            return
        blocks = []
        for offset, row in enumerate(selected):
            index = len(newest_first) - start - offset
            date = str(row.get("created_at", ""))[:19].replace("T", " ")
            rating = max(0, min(5, int(row.get("rating", 0))))
            comment = str(row.get("message", "")).strip() or "Комментария нет."
            blocks.append(f"Отзыв №{index} · {'★' * rating}{'☆' * (5 - rating)} · {date}\n{comment}")
        text = "\n\n──────────────\n\n".join(blocks)
        text += f"\n\nСтраница {page}/{page_count}. Следующая: /feedbacks {page + 1}" if page < page_count else f"\n\nСтраница {page}/{page_count}."
        for offset in range(0, len(text), 3800):
            await message.answer(text[offset:offset + 3800])

    def register_routes(self, app: web.Application) -> None:
        app.router.add_get("/app", self.app_page)
        app.router.add_get("/api/catalog", self.catalog)
        app.router.add_get("/api/mine", self.mine)
        app.router.add_get("/api/chats", self.chats)
        app.router.add_post("/api/create", self.create)
        app.router.add_route("PATCH", "/api/character/{character_id}", self.visibility)
        app.router.add_get("/api/chat/{character_id}", self.get_chat)
        app.router.add_post("/api/chat/{character_id}", self.send_chat)
        app.router.add_route("PATCH", "/api/chat/{character_id}/memory", self.update_chat_memory)
        app.router.add_get("/feedback-ui.js", self.feedback_ui_script)
        app.router.add_post("/api/feedback", self.submit_feedback)

    async def app_page(self, request: web.Request) -> web.Response:
        return web.Response(text=APP_HTML, content_type="text/html", headers={"Cache-Control": "no-store"})

    async def feedback_ui_script(self, request: web.Request) -> web.Response:
        return web.Response(
            text=FEEDBACK_UI_JS,
            content_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    async def submit_feedback(self, request: web.Request) -> web.Response:
        self._user_id(request)  # Validate the Telegram launch, but keep feedback anonymous.
        try:
            data = await request.json()
        except (json.JSONDecodeError, ValueError):
            raise web.HTTPBadRequest(
                text='{"error":"Не удалось прочитать отзыв."}',
                content_type="application/json",
            )
        if not isinstance(data, dict):
            raise web.HTTPBadRequest(
                text='{"error":"Проверь поля отзыва."}',
                content_type="application/json",
            )
        try:
            rating = int(data.get("rating", 0))
        except (ValueError, TypeError):
            rating = 0
        message = str(data.get("message", "")).strip()
        if rating not in range(1, 6) or len(message) > 1500:
            raise web.HTTPBadRequest(
                text='{"error":"Выбери оценку от 1 до 5 и проверь длину комментария."}',
                content_type="application/json",
            )
        row = {
            "rating": rating,
            "message": message,
            "created_at": _utc_now(),
        }
        try:
            self.feedback_store.add(row)
        except OSError as error:
            print(f"Could not save Mini App feedback: {type(error).__name__}")
            raise web.HTTPInternalServerError(
                text='{"error":"Не удалось сохранить отзыв. Попробуй позже."}',
                content_type="application/json",
            )
        return self._response({"ok": True})

    async def open_app_command(self, message: types.Message) -> None:
        if message.chat.type != ChatType.PRIVATE:
            await message.answer("Открой каталог персонажей в личном чате со мной: отправь /characters.")
            return
        if not self.app_url:
            await message.answer("Каталог персонажей пока недоступен: нужен публичный HTTPS-адрес сервера.")
            return
        await message.answer("Открывай каталог, создавай персонажей и общайся с ними:", reply_markup=self.keyboard())

    def _user_id(self, request: web.Request) -> int:
        raw = request.headers.get("X-Telegram-Init-Data", "")
        if not raw or len(raw) > 8192:
            raise web.HTTPUnauthorized(text=json.dumps({"error": "Открой приложение из Telegram."}), content_type="application/json")
        try:
            values = dict(parse_qsl(raw, keep_blank_values=True, strict_parsing=True))
            received_hash = values.pop("hash")
            auth_date = int(values["auth_date"])
            user = json.loads(values["user"])
            user_id = int(user["id"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise web.HTTPUnauthorized(text=json.dumps({"error": "Не удалось проверить пользователя Telegram."}), content_type="application/json")
        check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
        secret_key = hmac.new(b"WebAppData", self.bot_token.encode("utf-8"), hashlib.sha256).digest()
        expected_hash = hmac.new(secret_key, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected_hash, received_hash):
            print("Telegram Mini App authorization rejected: HMAC verification failed.")
            raise web.HTTPUnauthorized(text=json.dumps({"error": "Telegram не подтвердил подпись запуска. Отправь /characters и открой новое сообщение."}), content_type="application/json")
        age_seconds = int(time.time()) - auth_date
        if age_seconds > INIT_DATA_MAX_AGE_SECONDS:
            print(f"Telegram Mini App authorization expired: age_seconds={age_seconds}.")
            raise web.HTTPUnauthorized(text=json.dumps({"error": "Данные входа Telegram устарели. Закрой Mini App полностью, отправь /characters и открой приложение из нового сообщения."}), content_type="application/json")
        if age_seconds < -INIT_DATA_FUTURE_SKEW_SECONDS:
            print(f"Telegram Mini App authorization timestamp is in the future: age_seconds={age_seconds}.")
            raise web.HTTPUnauthorized(text=json.dumps({"error": "Время на сервере бота не совпадает с временем Telegram. Попробуй открыть приложение позже."}), content_type="application/json")
        return user_id

    async def _json(self, request: web.Request) -> dict:
        try:
            data = await request.json()
        except (json.JSONDecodeError, ValueError):
            raise web.HTTPBadRequest(text=json.dumps({"error": "Проверь отправленные данные."}), content_type="application/json")
        if not isinstance(data, dict):
            raise web.HTTPBadRequest(text=json.dumps({"error": "Неверный формат данных."}), content_type="application/json")
        return data

    def _response(self, data, status: int = 200) -> web.Response:
        return web.json_response(data, status=status, dumps=lambda obj: json.dumps(obj, ensure_ascii=False))

    async def catalog(self, request: web.Request) -> web.Response:
        user_id = self._user_id(request)
        try:
            return self._response({"characters": self.store.visible_to(user_id)})
        except (OSError, json.JSONDecodeError) as error:
            print(f"Ошибка чтения каталога персонажей: {error}")
            return self._response({"error": "Не удалось загрузить каталог."}, 500)

    async def mine(self, request: web.Request) -> web.Response:
        user_id = self._user_id(request)
        try:
            return self._response({"characters": self.store.mine(user_id)})
        except (OSError, json.JSONDecodeError) as error:
            print(f"Ошибка чтения персонажей пользователя: {error}")
            return self._response({"error": "Не удалось загрузить твои персонажи."}, 500)

    async def chats(self, request: web.Request) -> web.Response:
        user_id = self._user_id(request)
        try:
            return self._response({"chats": self.store.list_chats(user_id)})
        except (OSError, json.JSONDecodeError) as error:
            print(f"Ошибка чтения чатов персонажей: {error}")
            return self._response({"error": "Не удалось загрузить чаты."}, 500)

    async def create(self, request: web.Request) -> web.Response:
        user_id = self._user_id(request)
        data = await self._json(request)
        name = str(data.get("name", "")).strip()
        description = str(data.get("description", "")).strip()
        definition = str(data.get("definition", "")).strip()
        greeting = str(data.get("greeting", "")).strip()
        avatar = data.get("avatar", "")
        if not name or len(name) > 32 or len(description) > 120 or not definition or len(definition) > 2000 or len(greeting) > 400:
            return self._response({"error": "Заполни имя и характер. Проверь длину полей."}, 400)
        if not isinstance(avatar, str) or len(avatar) > 350000 or (avatar and not re.fullmatch(r"data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+", avatar)):
            return self._response({"error": "Фото должно быть в формате PNG, JPG или WebP и занимать меньше 250 КБ."}, 400)
        values = {
            "name": name,
            "description": description,
            "definition": definition,
            "greeting": greeting or f"Привет! Я {name}. Давай знакомиться.",
            "avatar": avatar,
            "is_public": bool(data.get("is_public", True)),
        }
        try:
            character = self.store.create_character(user_id, values)
        except (OSError, json.JSONDecodeError) as error:
            print(f"Ошибка сохранения персонажа: {error}")
            return self._response({"error": "Не удалось сохранить персонажа. Попробуй ещё раз."}, 500)
        return self._response({"character": character}, 201)

    async def visibility(self, request: web.Request) -> web.Response:
        user_id = self._user_id(request)
        data = await self._json(request)
        try:
            updated = self.store.set_visibility(request.match_info["character_id"], user_id, bool(data.get("is_public")))
        except (OSError, json.JSONDecodeError) as error:
            print(f"Ошибка изменения видимости персонажа: {error}")
            return self._response({"error": "Не удалось изменить настройки персонажа."}, 500)
        if not updated:
            return self._response({"error": "Персонаж не найден."}, 404)
        return self._response({"ok": True})

    async def get_chat(self, request: web.Request) -> web.Response:
        user_id = self._user_id(request)
        try:
            character = self.store.get_character(request.match_info["character_id"], user_id)
            if not character:
                return self._response({"error": "Персонаж не найден или скрыт."}, 404)
            messages = self.store.chat_history(user_id, character["id"])
            memory = self.store.chat_memory(user_id, character["id"])
            if not messages and character.get("greeting"):
                messages = [{"role": "assistant", "content": character["greeting"]}]
            return self._response({"character": self.store._public_character(character), "messages": messages, "memory": memory})
        except (OSError, json.JSONDecodeError) as error:
            print(f"Ошибка загрузки диалога с персонажем: {error}")
            return self._response({"error": "Не удалось загрузить диалог."}, 500)

    async def update_chat_memory(self, request: web.Request) -> web.Response:
        user_id = self._user_id(request)
        data = await self._json(request)
        memory = str(data.get("memory", "")).strip()
        if len(memory) > 1200:
            return self._response({"error": "Память должна быть не длиннее 1200 символов."}, 400)
        try:
            character = self.store.get_character(request.match_info["character_id"], user_id)
            if not character:
                return self._response({"error": "Персонаж не найден или скрыт."}, 404)
            self.store.save_chat_memory(user_id, character["id"], memory)
            return self._response({"ok": True, "memory": memory})
        except (OSError, json.JSONDecodeError) as error:
            print(f"Ошибка сохранения памяти чата: {error}")
            return self._response({"error": "Не удалось сохранить память. Попробуй ещё раз."}, 500)

    async def send_chat(self, request: web.Request) -> web.Response:
        user_id = self._user_id(request)
        data = await self._json(request)
        text = str(data.get("text", "")).strip()
        if not text or len(text) > 4000:
            return self._response({"error": "Сообщение должно содержать от 1 до 4000 символов."}, 400)
        character_id = request.match_info["character_id"]
        lock = self.chat_locks.setdefault(f"{user_id}:{character_id}", asyncio.Lock())
        async with lock:
            try:
                character = self.store.get_character(character_id, user_id)
                if not character:
                    return self._response({"error": "Персонаж не найден или скрыт."}, 404)
                history = self.store.chat_history(user_id, character_id)
                if not history and character.get("greeting"):
                    history.append({"role": "assistant", "content": character["greeting"]})
                prompt_history = history[-18:] + [{"role": "user", "content": text}]
                memory = self.store.chat_memory(user_id, character_id)
                reply_character = dict(character)
                if memory:
                    reply_character["definition"] = (
                        str(character.get("definition", ""))
                        + "\n\nПостоянная память этого чата. Учитывай её как ранее установленные факты, "
                        "если пользователь не сообщил, что они изменились:\n"
                        + memory
                    )
                answer = await self.generate_reply(reply_character, prompt_history)
                if not answer:
                    return self._response({"error": "Персонаж не смог ответить. Попробуй ещё раз."}, 502)
                saved = history + [
                    {"role": "user", "content": text},
                    {"role": "assistant", "content": answer},
                ]
                self.store.save_chat(user_id, character_id, saved)
                return self._response({"answer": answer, "messages": saved[-40:]})
            except (OSError, json.JSONDecodeError) as error:
                print(f"Ошибка обработки диалога персонажа: {error}")
                return self._response({"error": "Не удалось сохранить диалог."}, 500)
            except Exception as error:
                print(f"Ошибка ответа персонажа Groq: {type(error).__name__}: {error}")
                return self._response({"error": "Персонаж временно не может ответить."}, 502)

