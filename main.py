import discord
from discord.ext import commands
from discord.ui import Button, View
from flask import Flask
from threading import Thread
import yt_dlp
import asyncio
import os
from collections import deque

# ========== CONFIGURAÇÕES ==========
TOKEN = os.getenv("TOKEN")
if TOKEN is None:
    TOKEN = ""
PREFIXO = "!"

# ========== SERVIDOR WEB ==========
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot online!"

def run():
    app.run(host='0.0.0.0', port=8080)

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

# ========== YT-DLP COM COOKIES ==========
YDL_OPTS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'no_warnings': True,
    'cookiefile': 'cookies.txt',
}

def buscar_musica(busca, usar_youtube=False):
    if 'spotify.com' in busca:
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(busca, download=False)
            artista = info.get('artist', '')
            titulo = info.get('title', '')
            busca = f"{artista} {titulo}"
    if usar_youtube or 'youtube.com' in busca or 'youtu.be' in busca:
        if not busca.startswith('http'): busca = f"ytsearch:{busca}"
        usar_youtube = True
    else:
        if not busca.startswith('http'): busca = f"scsearch:{busca}"
    with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
        info = ydl.extract_info(busca, download=False)
        if 'entries' in info and len(info['entries']) > 0: info = info['entries'][0]
        return {
            'url': info.get('url', ''),
            'titulo': info.get('title', 'Desconhecido'),
            'artista': info.get('uploader', 'Desconhecido'),
            'thumb': info.get('thumbnail', ''),
            'fonte': 'YouTube' if usar_youtube else 'SoundCloud',
        }

async def tocar_fila(vc):
    global musica_atual, modo_loop
    if not vc or not vc.is_connected(): return
    if modo_loop and musica_atual:
        try:
            source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(musica_atual['url'], before_options="-reconnect 1 -reconnect_streamed 1"), volume=volume_do_bot)
            vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(tocar_fila(vc), bot.loop))
        except: musica_atual = None; asyncio.run_coroutine_threadsafe(tocar_fila(vc), bot.loop)
        return
    while fila_de_musicas:
        info = fila_de_musicas.popleft()
        if not info.get('url'): continue
        try:
            musica_atual = info
            source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(info['url'], before_options="-reconnect 1 -reconnect_streamed 1"), volume=volume_do_bot)
            vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(tocar_fila(vc), bot.loop))
            return
        except: musica_atual = None; continue
    musica_atual = None

class Painel(View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="⏯️", style=discord.ButtonStyle.green)
    async def b_pause(self, i, b):
        vc = i.guild.voice_client
        if vc and vc.is_playing(): vc.pause(); await i.response.send_message("⏸️ Pausado", ephemeral=True)
        elif vc and vc.is_paused(): vc.resume(); await i.response.send_message("▶️ Tocando", ephemeral=True)
        else: await i.response.send_message("❌ Nada", ephemeral=True)
    @discord.ui.button(label="⏭️", style=discord.ButtonStyle.gray)
    async def b_skip(self, i, b):
        vc = i.guild.voice_client
        if vc and vc.is_playing(): vc.stop(); await i.response.send_message("⏭️ Pulando", ephemeral=True)
        else: await i.response.send_message("❌ Nada", ephemeral=True)
    @discord.ui.button(label="🔁", style=discord.ButtonStyle.blurple)
    async def b_loop(self, i, b):
        global modo_loop; modo_loop = not modo_loop
        await i.response.send_message(f"Loop {'ON' if modo_loop else 'OFF'}", ephemeral=True)
    @discord.ui.button(label="🛑", style=discord.ButtonStyle.red)
    async def b_stop(self, i, b):
        global fila_de_musicas, modo_loop
        vc = i.guild.voice_client
        if vc: fila_de_musicas.clear(); modo_loop = False; vc.stop(); await vc.disconnect(); await i.response.send_message("🛑 Parou", ephemeral=True)
        else: await i.response.send_message("❌ Nada", ephemeral=True)

@bot.command()
async def painel(ctx):
    embed = discord.Embed(title="🎵 Painel", color=0xFF0000 if musica_atual and musica_atual.get('fonte')=='YouTube' else 0x8A2BE2)
    if musica_atual:
        embed.add_field(name="🎧 Tocando", value=f"**{musica_atual['titulo'][:100]}**", inline=False)
        if musica_atual['thumb']: embed.set_thumbnail(url=musica_atual['thumb'])
    if fila_de_musicas:
        txt = "".join(f"`{i}.` {m['titulo'][:40]}\n" for i,m in enumerate(fila_de_musicas,1))
        embed.add_field(name="📋 Fila", value=txt[:1024], inline=False)
    embed.add_field(name="🔁", value="ON" if modo_loop else "OFF")
    embed.add_field(name="🔊", value=f"{int(volume_do_bot*100)}%")
    await ctx.send(embed=embed, view=Painel())

@bot.command()
async def play(ctx, *, busca=None):
    if busca is None: await ctx.send("❌ Use: `!play nome`"); return
    if not ctx.author.voice: await ctx.send("❌ Entre num canal de voz!"); return
    canal = ctx.author.voice.channel
    if ctx.voice_client is None: vc = await canal.connect()
    else: vc = ctx.voice_client; await vc.move_to(canal) if vc.channel != canal else None
    await ctx.send(f"🔎 Procurando: `{busca}`...")
    info = None
    for yt in [False, True]:
        try:
            info = await asyncio.to_thread(buscar_musica, busca, yt)
            if info and info.get('url'): break
        except: continue
    if not info or not info.get('url'): await ctx.send("❌ Não encontrei."); return
    if vc.is_playing() or vc.is_paused():
        fila_de_musicas.append(info)
        await ctx.send(f"📋 **{info['titulo'][:80]}** adicionada!")
    else:
        global musica_atual; musica_atual = info
        try:
            source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(info['url'], before_options="-reconnect 1 -reconnect_streamed 1"), volume=volume_do_bot)
            vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(tocar_fila(vc), bot.loop))
            embed = discord.Embed(title="🎧 Tocando agora", description=f"**{info['titulo'][:100]}**", color=0xFF0000 if info.get('fonte')=='YouTube' else 0xFF5500)
            if info['thumb']: embed.set_thumbnail(url=info['thumb'])
            embed.add_field(name="👤", value=info['artista'][:50]); embed.add_field(name="🌐", value=info.get('fonte','?'))
            await ctx.send(embed=embed)
        except: await ctx.send("❌ Erro ao tocar."); musica_atual = None

@bot.command()
async def yt(ctx, *, busca=None):
    if busca is None: await ctx.send("❌ Use: `!yt nome`"); return
    if not ctx.author.voice: await ctx.send("❌ Entre num canal de voz!"); return
    canal = ctx.author.voice.channel
    if ctx.voice_client is None: vc = await canal.connect()
    else: vc = ctx.voice_client; await vc.move_to(canal) if vc.channel != canal else None
    await ctx.send(f"🔎 YouTube: `{busca}`...")
    try: info = await asyncio.to_thread(buscar_musica, busca, True)
    except Exception as e: await ctx.send(f"❌ Erro: {str(e)[:100]}"); return
    if not info or not info.get('url'): await ctx.send("❌ Não encontrei."); return
    if vc.is_playing() or vc.is_paused():
        fila_de_musicas.append(info); await ctx.send(f"📋 **{info['titulo'][:80]}** adicionada!")
    else:
        global musica_atual; musica_atual = info
        try:
            source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(info['url'], before_options="-reconnect 1 -reconnect_streamed 1"), volume=volume_do_bot)
            vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(tocar_fila(vc), bot.loop))
            embed = discord.Embed(title="🎧 Tocando (YouTube)", description=f"**{info['titulo'][:100]}**", color=0xFF0000)
            if info['thumb']: embed.set_thumbnail(url=info['thumb'])
            embed.add_field(name="👤", value=info['artista'][:50]); await ctx.send(embed=embed)
        except: await ctx.send("❌ Erro ao tocar."); musica_atual = None

@bot.command()
async def fila(ctx):
    if not fila_de_musicas: await ctx.send("📋 Fila vazia."); return
    txt = "**📋 Fila:**\n" + "".join(f"`{i}.` {m['titulo'][:50]}\n" for i,m in enumerate(fila_de_musicas,1))
    await ctx.send(txt[:2000])

@bot.command()
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing(): ctx.voice_client.stop(); await ctx.send("⏭️ Pulando!")
    else: await ctx.send("❌ Nada tocando.")

@bot.command()
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing(): ctx.voice_client.pause(); await ctx.send("⏸️ Pausado!")
    else: await ctx.send("❌ Nada tocando.")

@bot.command()
async def continuar(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused(): ctx.voice_client.resume(); await ctx.send("▶️ Tocando!")
    else: await ctx.send("❌ Não está pausado.")

@bot.command()
async def loop(ctx):
    global modo_loop; modo_loop = not modo_loop
    await ctx.send(f"Loop {'ON' if modo_loop else 'OFF'}!")

@bot.command()
async def volume(ctx, vol: int = None):
    global volume_do_bot
    if vol is None: await ctx.send(f"🔊 {int(volume_do_bot*100)}%"); return
    if vol < 0 or vol > 100: await ctx.send("❌ 0-100"); return
    volume_do_bot = vol/100
    if ctx.voice_client and ctx.voice_client.source and isinstance(ctx.voice_client.source, discord.PCMVolumeTransformer):
        ctx.voice_client.source.volume = volume_do_bot
    await ctx.send(f"🔊 **{vol}%**")

@bot.command()
async def limpar(ctx): fila_de_musicas.clear(); await ctx.send("🧹 Fila limpa!")

@bot.command()
async def parar(ctx):
    global fila_de_musicas, modo_loop, musica_atual
    fila_de_musicas.clear(); modo_loop = False; musica_atual = None
    if ctx.voice_client: ctx.voice_client.stop(); await ctx.voice_client.disconnect(); await ctx.send("⏹️ Até mais!")

@bot.command()
async def tocando(ctx):
    if musica_atual:
        info = musica_atual
        embed = discord.Embed(title="🎧 Tocando", description=f"**{info['titulo'][:100]}**", color=0xFF0000 if info.get('fonte')=='YouTube' else 0x8A2BE2)
        if info['thumb']: embed.set_thumbnail(url=info['thumb'])
        embed.add_field(name="👤", value=info['artista'][:50]); await ctx.send(embed=embed)
    else: await ctx.send("❌ Nada.")

@bot.command()
async def remover(ctx, posicao: int = None):
    global fila_de_musicas
    if posicao is None or posicao < 1 or posicao > len(fila_de_musicas): await ctx.send("❌ `!remover <n>`"); return
    lista = list(fila_de_musicas); removida = lista.pop(posicao-1); fila_de_musicas = deque(lista)
    await ctx.send(f"🗑️ {removida['titulo'][:50]}")

@bot.command()
async def comandos(ctx):
    embed = discord.Embed(title="🎵 Comandos do Dante", color=0xFF0000)
    embed.add_field(name="🎧", value="`!play` `!yt` `!pause` `!continuar` `!skip`", inline=False)
    embed.add_field(name="📋", value="`!fila` `!remover` `!limpar`", inline=False)
    embed.add_field(name="⚙️", value="`!painel` `!loop` `!volume` `!parar`", inline=False)
    await ctx.send(embed=embed)

@bot.event
async def on_ready():
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="!comandos | YouTube + SoundCloud"))
    print(f"✅ Bot online no Render!")

bot.run(TOKEN)
