import discord
from discord.ext import commands
from discord.ui import Button, View
from flask import Flask
from threading import Thread
import yt_dlp
import asyncio
import os
import hashlib
from collections import deque

# ========== CONFIGURAÇÕES ==========
TOKEN = os.getenv("TOKEN")
if TOKEN is None: TOKEN = ""
PREFIXO = "!"

# ========== SERVIDOR WEB (UptimeRobot) ==========
app = Flask(__name__)
@app.route('/')
def home(): return "Bot online!"
def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
Thread(target=run).start()

# ========== BOT ==========
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix=PREFIXO, intents=intents)

# ========== VARIÁVEIS ==========
fila_de_musicas = deque()
musica_atual = None
modo_loop = False
volume_do_bot = 0.5

# ========== CACHE ==========
CACHE_DIR = "/tmp/dante_cache"
os.makedirs(CACHE_DIR, exist_ok=True)

# ========== YT-DLP OTIMIZADO ==========
YDL_OPTIONS = {
    'format': 'ba/ba*/bestaudio/best',
    'noplaylist': True,
    'extract_flat': False,
    'quiet': True,
    'no_warnings': True,
    'source_address': '0.0.0.0',
    'force_ipv4': True,
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
}

# Opções do FFmpeg para streaming estável
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

def buscar_musica(busca):
    """Busca no YouTube ou SoundCloud"""
    if 'spotify.com' in busca:
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(busca, download=False)
            artista = info.get('artist', ''); titulo = info.get('title', '')
            busca = f"{artista} {titulo}"
    
    # Se for link do YouTube ou SoundCloud, usa direto
    if 'youtube.com' in busca or 'youtu.be' in busca:
        query = busca
        fonte = 'YouTube'
    elif 'soundcloud.com' in busca:
        query = busca
        fonte = 'SoundCloud'
    else:
        # Busca por nome: tenta YouTube primeiro
        query = f"ytsearch1:{busca}"
        fonte = 'YouTube'
    
    try:
        with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
            info = ydl.extract_info(query, download=False)
            if 'entries' in info and len(info['entries']) > 0:
                info = info['entries'][0]
            
            return {
                'url': info.get('url', ''),
                'titulo': info.get('title', 'Desconhecido'),
                'artista': info.get('uploader', 'Desconhecido'),
                'thumb': info.get('thumbnail', ''),
                'duracao': info.get('duration', 0) or 0,
                'fonte': fonte,
            }
    except:
        # Fallback: tenta SoundCloud
        try:
            with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
                info = ydl.extract_info(f"scsearch:{busca}", download=False)
                if 'entries' in info and len(info['entries']) > 0:
                    info = info['entries'][0]
                return {
                    'url': info.get('url', ''),
                    'titulo': info.get('title', 'Desconhecido'),
                    'artista': info.get('uploader', 'Desconhecido'),
                    'thumb': info.get('thumbnail', ''),
                    'duracao': info.get('duration', 0) or 0,
                    'fonte': 'SoundCloud',
                }
        except:
            raise Exception("Não encontrei em nenhuma plataforma.")

async def baixar_cache(url):
    nome_hash = hashlib.md5(url.encode()).hexdigest()[:12]
    cache_path = os.path.join(CACHE_DIR, f"{nome_hash}.mp3")
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0: return cache_path
    ydl_opts = {
        'format': 'ba/best', 'outtmpl': cache_path.replace('.mp3', ''),
        'quiet': True, 'no_warnings': True,
        'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '128'}],
        'retries': 3, 'fragment_retries': 3,
    }
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_opts).download([url]))
    return cache_path

async def tocar_fila(vc):
    global musica_atual, modo_loop
    if not vc or not vc.is_connected(): return
    if modo_loop and musica_atual:
        try:
            src = musica_atual.get('cache', musica_atual['url'])
            source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(src, **FFMPEG_OPTIONS), volume=volume_do_bot)
            vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(tocar_fila(vc), bot.loop))
        except: musica_atual = None; asyncio.run_coroutine_threadsafe(tocar_fila(vc), bot.loop)
        return
    while fila_de_musicas:
        info = fila_de_musicas.popleft()
        if not info.get('url'): continue
        try:
            musica_atual = info
            src = info.get('cache', info['url'])
            source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(src, **FFMPEG_OPTIONS), volume=volume_do_bot)
            vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(tocar_fila(vc), bot.loop))
            return
        except: musica_atual = None; continue
    musica_atual = None

class Painel(View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="⏯️", style=discord.ButtonStyle.green)
    async def b_pause(self, i, b):
        vc = i.guild.voice_client
        if vc and vc.is_playing(): vc.pause(); await i.response.send_message("⏸️", ephemeral=True)
        elif vc and vc.is_paused(): vc.resume(); await i.response.send_message("▶️", ephemeral=True)
    @discord.ui.button(label="⏭️", style=discord.ButtonStyle.gray)
    async def b_skip(self, i, b):
        vc = i.guild.voice_client
        if vc and vc.is_playing(): vc.stop(); await i.response.send_message("⏭️", ephemeral=True)
    @discord.ui.button(label="🔁", style=discord.ButtonStyle.blurple)
    async def b_loop(self, i, b):
        global modo_loop; modo_loop = not modo_loop
        await i.response.send_message(f"🔁 {'ON' if modo_loop else 'OFF'}", ephemeral=True)
    @discord.ui.button(label="🛑", style=discord.ButtonStyle.red)
    async def b_stop(self, i, b):
        global fila_de_musicas, modo_loop
        vc = i.guild.voice_client
        if vc: fila_de_musicas.clear(); modo_loop = False; vc.stop(); await vc.disconnect()

@bot.command()
async def play(ctx, *, busca=None):
    if busca is None: await ctx.send("❌ Use: `!play nome ou link`"); return
    if not ctx.author.voice: await ctx.send("❌ Entre num canal de voz!"); return
    canal = ctx.author.voice.channel
    vc = ctx.voice_client or await canal.connect()
    if vc.channel != canal: await vc.move_to(canal)
    await ctx.send(f"🔎 Buscando: `{busca}`...")
    try: info = await asyncio.to_thread(buscar_musica, busca)
    except Exception as e: await ctx.send(f"❌ {str(e)[:100]}"); return
    if not info or not info.get('url'): await ctx.send("❌ Não encontrei."); return
    
    # Cache para músicas longas
    if info.get('duracao', 0) > 900:
        await ctx.send("⏳ Música longa, preparando cache...")
        try: info['cache'] = await baixar_cache(info['url'])
        except: pass
    
    if vc.is_playing() or vc.is_paused():
        fila_de_musicas.append(info)
        await ctx.send(f"📋 `{info['titulo'][:80]}` adicionada!")
    else:
        global musica_atual; musica_atual = info
        try:
            src = info.get('cache', info['url'])
            source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(src, **FFMPEG_OPTIONS), volume=volume_do_bot)
            vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(tocar_fila(vc), bot.loop))
            embed = discord.Embed(title="🎧 Tocando", description=f"**{info['titulo'][:100]}**", color=0xFF0000)
            if info['thumb']: embed.set_thumbnail(url=info['thumb'])
            embed.add_field(name="👤", value=info['artista'][:50])
            embed.add_field(name="🌐", value=info.get('fonte','?'))
            if info.get('duracao',0)>0: embed.add_field(name="⏱️", value=f"{info['duracao']//60}min")
            await ctx.send(embed=embed)
        except: await ctx.send("❌ Erro ao tocar."); musica_atual = None

@bot.command()
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing(): ctx.voice_client.stop(); await ctx.send("⏭️")
@bot.command()
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing(): ctx.voice_client.pause(); await ctx.send("⏸️")
@bot.command()
async def continuar(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused(): ctx.voice_client.resume(); await ctx.send("▶️")
@bot.command()
async def loop(ctx):
    global modo_loop; modo_loop = not modo_loop; await ctx.send(f"🔁 {'ON' if modo_loop else 'OFF'}")
@bot.command()
async def volume(ctx, vol: int = None):
    global volume_do_bot
    if vol is None: await ctx.send(f"🔊 {int(volume_do_bot*100)}%"); return
    if vol < 0 or vol > 100: await ctx.send("❌ 0-100"); return
    volume_do_bot = vol/100
    if ctx.voice_client and ctx.voice_client.source and isinstance(ctx.voice_client.source, discord.PCMVolumeTransformer):
        ctx.voice_client.source.volume = volume_do_bot
    await ctx.send(f"🔊 {vol}%")
@bot.command()
async def fila(ctx):
    if not fila_de_musicas: await ctx.send("📋 Vazia."); return
    txt = "".join(f"`{i}.` {m['titulo'][:50]}\n" for i,m in enumerate(fila_de_musicas,1))
    await ctx.send(f"**📋 Fila:**\n{txt[:2000]}")
@bot.command()
async def limpar(ctx): fila_de_musicas.clear(); await ctx.send("🧹")
@bot.command()
async def parar(ctx):
    global fila_de_musicas, modo_loop, musica_atual
    fila_de_musicas.clear(); modo_loop = False; musica_atual = None
    if ctx.voice_client: ctx.voice_client.stop(); await ctx.voice_client.disconnect(); await ctx.send("⏹️")
@bot.command()
async def painel(ctx):
    embed = discord.Embed(title="🎵 Painel", color=0xFF0000)
    if musica_atual: embed.add_field(name="🎧", value=musica_atual['titulo'][:100], inline=False)
    if fila_de_musicas: embed.add_field(name="📋", value="".join(f"`{i}.` {m['titulo'][:40]}\n" for i,m in enumerate(fila_de_musicas,1))[:1024])
    embed.add_field(name="🔁", value="ON" if modo_loop else "OFF")
    embed.add_field(name="🔊", value=f"{int(volume_do_bot*100)}%")
    await ctx.send(embed=embed, view=Painel())
@bot.command()
async def comandos(ctx):
    embed = discord.Embed(title="🎵 Comandos", color=0xFF0000)
    embed.add_field(name="🎧", value="`!play nome/link` `!pause` `!continuar` `!skip`", inline=False)
    embed.add_field(name="📋", value="`!fila` `!remover` `!limpar`", inline=False)
    embed.add_field(name="⚙️", value="`!painel` `!loop` `!volume` `!parar`", inline=False)
    await ctx.send(embed=embed)
@bot.command()
async def remover(ctx, posicao: int = None):
    global fila_de_musicas
    if posicao is None or posicao < 1 or posicao > len(fila_de_musicas): await ctx.send("❌ `!remover <n>`"); return
    lista = list(fila_de_musicas); removida = lista.pop(posicao-1); fila_de_musicas = deque(lista)
    await ctx.send(f"🗑️ {removida['titulo'][:50]}")
@bot.command()
async def tocando(ctx):
    if musica_atual:
        info = musica_atual
        embed = discord.Embed(title="🎧 Tocando", description=f"**{info['titulo'][:100]}**", color=0xFF0000)
        if info['thumb']: embed.set_thumbnail(url=info['thumb'])
        embed.add_field(name="👤", value=info['artista'][:50])
        embed.add_field(name="🌐", value=info.get('fonte','?'))
        if info.get('duracao',0)>0: embed.add_field(name="⏱️", value=f"{info['duracao']//60}min")
        await ctx.send(embed=embed)
    else: await ctx.send("❌ Nada.")

@bot.event
async def on_ready():
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="!comandos"))
    print(f"✅ Bot online!")

bot.run(TOKEN)
