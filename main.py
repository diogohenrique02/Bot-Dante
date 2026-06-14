import imageio_ffmpeg
import os
os.environ["PATH"] += os.pathsep + os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe())

import discord
from discord.ext import commands
from discord.ui import View
from flask import Flask
from threading import Thread
import yt_dlp
import asyncio
import hashlib
import urllib.request
import urllib.parse
import json
from collections import deque

# ========== CONFIGURAÇÕES ==========
TOKEN = os.getenv("TOKEN", "")
PREFIXO = "!"
TEMP_DIR = "/tmp/dante_audio"
os.makedirs(TEMP_DIR, exist_ok=True)

# Cookies (opcional — melhora acesso a vídeos restritos)
COOKIES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
if not os.path.exists(COOKIES_PATH):
    print(f"⚠️  cookies.txt não encontrado em {COOKIES_PATH}")
    COOKIES_PATH = None

# ========== INVIDIOUS — lista de instâncias públicas como fallback ==========
# O bot tenta cada instância em ordem até uma funcionar
INVIDIOUS_INSTANCES = [
    "https://inv.nadeko.net",
    "https://invidious.privacydev.net",
    "https://yt.cdaut.de",
    "https://invidious.nerdvpn.de",
    "https://invidious.slipfox.xyz",
]

def buscar_via_invidious(busca: str) -> dict:
    """Busca música via API do Invidious (sem bloqueio de IP de datacenter)."""
    query = urllib.parse.quote(busca)
    erros = []

    for instancia in INVIDIOUS_INSTANCES:
        try:
            url = f"{instancia}/api/v1/search?q={query}&type=video&fields=videoId,title,author,lengthSeconds,videoThumbnails&page=1"
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0',
                'Accept': 'application/json',
            })
            with urllib.request.urlopen(req, timeout=8) as resp:
                resultados = json.loads(resp.read().decode())

            if not resultados:
                erros.append(f"{instancia}: sem resultados")
                continue

            video = resultados[0]
            video_id = video.get('videoId', '')
            if not video_id:
                continue

            # Pega a melhor thumbnail disponível
            thumbs = video.get('videoThumbnails', [])
            thumb = thumbs[-1]['url'] if thumbs else ''
            if thumb and thumb.startswith('/'):
                thumb = instancia + thumb

            print(f"[Invidious] Encontrado via {instancia}: {video.get('title','?')}")
            return {
                'video_id': video_id,
                'webpage_url': f"https://www.youtube.com/watch?v={video_id}",
                'titulo': video.get('title', 'Desconhecido'),
                'artista': video.get('author', 'Desconhecido'),
                'thumb': thumb,
                'duracao': int(video.get('lengthSeconds') or 0),
            }
        except Exception as ex:
            erros.append(f"{instancia}: {ex}")
            continue

    raise Exception(f"Invidious falhou em todas as instâncias: {'; '.join(erros)}")

# ========== YT-DLP ==========
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

def _ydl_base() -> dict:
    opts = {
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'source_address': '0.0.0.0',
        'force_ipv4': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['tv_embedded', 'android', 'web'],
            }
        },
        'http_headers': {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            )
        },
    }
    if COOKIES_PATH:
        opts['cookiefile'] = COOKIES_PATH
    return opts

def buscar_info(busca: str) -> dict:
    """
    Busca a música:
    1. Se for link do YouTube, usa yt-dlp direto
    2. Se for busca por texto, usa Invidious (sem bloqueio de IP)
    """
    # Link direto — usa yt-dlp
    if any(x in busca for x in ('youtube.com', 'youtu.be')):
        opts = _ydl_base()
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(busca, download=False)
            if 'entries' in info:
                info = info['entries'][0]
            return {
                'video_id': info.get('id', ''),
                'webpage_url': info.get('webpage_url', busca),
                'titulo': info.get('title', 'Desconhecido'),
                'artista': info.get('uploader', 'Desconhecido'),
                'thumb': info.get('thumbnail', ''),
                'duracao': int(info.get('duration') or 0),
            }

    # Busca por texto — usa Invidious para evitar bloqueio de IP
    return buscar_via_invidious(busca)

def baixar_audio(webpage_url: str, video_id: str) -> str:
    """Baixa o áudio para arquivo .mp3 e retorna o caminho."""
    nome = hashlib.md5(video_id.encode()).hexdigest()[:12]
    caminho = os.path.join(TEMP_DIR, nome)
    caminho_mp3 = caminho + ".mp3"

    # Cache — já baixado antes
    if os.path.exists(caminho_mp3) and os.path.getsize(caminho_mp3) > 10000:
        print(f"[Cache] Usando arquivo em cache: {caminho_mp3}")
        return caminho_mp3

    opts = _ydl_base()
    opts.update({
        'format': 'bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio/best',
        'outtmpl': caminho,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '128',
        }],
        'ffmpeg_location': os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe()),
        'retries': 3,
    })

    print(f"[Download] Baixando: {webpage_url}")
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([webpage_url])

    # yt-dlp pode salvar com extensão diferente — renomeia para .mp3
    if not os.path.exists(caminho_mp3):
        for f in os.listdir(TEMP_DIR):
            if f.startswith(nome) and not f.endswith('.mp3'):
                os.rename(os.path.join(TEMP_DIR, f), caminho_mp3)
                break

    if not os.path.exists(caminho_mp3):
        raise Exception("Arquivo de áudio não foi criado após download.")

    print(f"[Download] Concluído: {caminho_mp3} ({os.path.getsize(caminho_mp3)//1024}KB)")
    return caminho_mp3

def limpar_cache():
    """Remove arquivos mais antigos se cache passar de 400MB."""
    try:
        arquivos = [
            (os.path.join(TEMP_DIR, f), os.path.getmtime(os.path.join(TEMP_DIR, f)))
            for f in os.listdir(TEMP_DIR)
        ]
        total = sum(os.path.getsize(a) for a, _ in arquivos)
        if total > 400 * 1024 * 1024:
            arquivos.sort(key=lambda x: x[1])
            for caminho, _ in arquivos[:len(arquivos)//2]:
                os.remove(caminho)
    except:
        pass

# ========== SERVIDOR WEB ==========
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot online!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

Thread(target=run_web, daemon=True).start()

# ========== BOT ==========
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix=PREFIXO, intents=intents, help_command=None)

# ========== ESTADO POR SERVIDOR ==========
class EstadoServidor:
    def __init__(self):
        self.fila: deque = deque()
        self.musica_atual: dict | None = None
        self.modo_loop: bool = False
        self.volume: float = 0.5

_estados: dict[int, EstadoServidor] = {}

def estado(guild_id: int) -> EstadoServidor:
    if guild_id not in _estados:
        _estados[guild_id] = EstadoServidor()
    return _estados[guild_id]

# ========== PLAYER ==========
async def tocar_proximo(vc: discord.VoiceClient, guild_id: int):
    e = estado(guild_id)
    if not vc or not vc.is_connected():
        return

    if e.modo_loop and e.musica_atual:
        info = e.musica_atual
    elif e.fila:
        info = e.fila.popleft()
        e.musica_atual = info
    else:
        e.musica_atual = None
        return

    try:
        caminho = info.get('caminho_audio')
        if not caminho or not os.path.exists(caminho):
            print(f"[Player] Arquivo não encontrado: {caminho}")
            asyncio.run_coroutine_threadsafe(tocar_proximo(vc, guild_id), bot.loop)
            return

        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(caminho),
            volume=e.volume
        )
        vc.play(
            source,
            after=lambda err: asyncio.run_coroutine_threadsafe(
                tocar_proximo(vc, guild_id), bot.loop
            )
        )
    except Exception as ex:
        print(f"[ERRO ao tocar] {ex}")
        e.musica_atual = None
        asyncio.run_coroutine_threadsafe(tocar_proximo(vc, guild_id), bot.loop)

# ========== PAINEL ==========
class Painel(View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="⏯️", style=discord.ButtonStyle.green)
    async def b_pause(self, interaction: discord.Interaction, button):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            await interaction.response.send_message("⏸️ Pausado", ephemeral=True)
        elif vc and vc.is_paused():
            vc.resume()
            await interaction.response.send_message("▶️ Retomado", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Nada tocando.", ephemeral=True)

    @discord.ui.button(label="⏭️", style=discord.ButtonStyle.gray)
    async def b_skip(self, interaction: discord.Interaction, button):
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await interaction.response.send_message("⏭️ Pulando...", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Nada tocando.", ephemeral=True)

    @discord.ui.button(label="🔁", style=discord.ButtonStyle.blurple)
    async def b_loop(self, interaction: discord.Interaction, button):
        e = estado(self.guild_id)
        e.modo_loop = not e.modo_loop
        await interaction.response.send_message(
            f"🔁 Loop {'**ativado**' if e.modo_loop else '**desativado**'}",
            ephemeral=True
        )

    @discord.ui.button(label="🛑", style=discord.ButtonStyle.red)
    async def b_stop(self, interaction: discord.Interaction, button):
        e = estado(self.guild_id)
        e.fila.clear()
        e.modo_loop = False
        e.musica_atual = None
        vc = interaction.guild.voice_client
        if vc:
            vc.stop()
            await vc.disconnect()
        await interaction.response.send_message("⏹️ Parado.", ephemeral=True)

# ========== HELPERS ==========
def formatar_duracao(seg: int) -> str:
    m, s = divmod(seg, 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}min" if h else f"{m}:{s:02d}"

def embed_musica(info: dict, titulo="🎧 Tocando agora") -> discord.Embed:
    embed = discord.Embed(
        title=titulo,
        description=f"**{info['titulo'][:100]}**",
        color=0xFF0000
    )
    if info.get('thumb'):
        embed.set_thumbnail(url=info['thumb'])
    embed.add_field(name="👤 Artista", value=info['artista'][:50], inline=True)
    if info.get('duracao', 0) > 0:
        embed.add_field(name="⏱️ Duração", value=formatar_duracao(info['duracao']), inline=True)
    return embed

async def conectar_vc(ctx) -> discord.VoiceClient | None:
    if not ctx.author.voice:
        await ctx.send("❌ Entre num canal de voz primeiro!")
        return None
    canal = ctx.author.voice.channel
    vc = ctx.voice_client
    if vc is None:
        vc = await canal.connect()
    elif vc.channel != canal:
        await vc.move_to(canal)
    return vc

# ========== COMANDOS ==========

@bot.command(name="yt", aliases=["play", "tocar"])
async def cmd_yt(ctx, *, busca: str = None):
    if not busca:
        await ctx.send("❌ Use: `!yt nome da música ou link`")
        return

    vc = await conectar_vc(ctx)
    if not vc:
        return

    msg = await ctx.send(f"🔎 Buscando: `{busca}`...")

    try:
        info = await asyncio.to_thread(buscar_info, busca)
    except Exception as ex:
        await msg.edit(content=f"❌ Não encontrei: {str(ex)[:120]}")
        return

    await msg.edit(content=f"⏳ Baixando **{info['titulo'][:60]}**...")

    try:
        caminho = await asyncio.to_thread(
            baixar_audio, info['webpage_url'], info['video_id']
        )
        info['caminho_audio'] = caminho
        limpar_cache()
    except Exception as ex:
        await msg.edit(content=f"❌ Erro ao baixar: {str(ex)[:120]}")
        return

    e = estado(ctx.guild.id)

    if vc.is_playing() or vc.is_paused():
        e.fila.append(info)
        await msg.edit(
            content=None,
            embed=discord.Embed(
                title="📋 Adicionado à fila",
                description=f"**{info['titulo'][:100]}**\nPosição: #{len(e.fila)}",
                color=0x5865F2
            )
        )
    else:
        e.musica_atual = info
        try:
            source = discord.PCMVolumeTransformer(
                discord.FFmpegPCMAudio(caminho),
                volume=e.volume
            )
            vc.play(
                source,
                after=lambda err: asyncio.run_coroutine_threadsafe(
                    tocar_proximo(vc, ctx.guild.id), bot.loop
                )
            )
            await msg.edit(content=None, embed=embed_musica(info))
        except Exception as ex:
            await msg.edit(content=f"❌ Erro ao reproduzir: {str(ex)[:100]}")
            e.musica_atual = None

@bot.command(name="skip", aliases=["pular"])
async def cmd_skip(ctx):
    vc = ctx.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
        await ctx.send("⏭️ Pulando...")
    else:
        await ctx.send("❌ Nada tocando.")

@bot.command(name="pause", aliases=["pausar"])
async def cmd_pause(ctx):
    vc = ctx.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await ctx.send("⏸️ Pausado.")
    else:
        await ctx.send("❌ Nada tocando.")

@bot.command(name="continuar", aliases=["resume"])
async def cmd_continuar(ctx):
    vc = ctx.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await ctx.send("▶️ Retomado.")
    else:
        await ctx.send("❌ Não está pausado.")

@bot.command(name="loop")
async def cmd_loop(ctx):
    e = estado(ctx.guild.id)
    e.modo_loop = not e.modo_loop
    await ctx.send(f"🔁 Loop {'**ativado**' if e.modo_loop else '**desativado**'}")

@bot.command(name="volume", aliases=["vol"])
async def cmd_volume(ctx, vol: int = None):
    e = estado(ctx.guild.id)
    if vol is None:
        await ctx.send(f"🔊 Volume: **{int(e.volume * 100)}%**")
        return
    if not 0 <= vol <= 100:
        await ctx.send("❌ Use 0–100.")
        return
    e.volume = vol / 100
    vc = ctx.voice_client
    if vc and vc.source and isinstance(vc.source, discord.PCMVolumeTransformer):
        vc.source.volume = e.volume
    await ctx.send(f"🔊 Volume: **{vol}%**")

@bot.command(name="fila", aliases=["queue", "q"])
async def cmd_fila(ctx):
    e = estado(ctx.guild.id)
    if not e.fila and not e.musica_atual:
        await ctx.send("📋 Fila vazia.")
        return
    linhas = []
    if e.musica_atual:
        linhas.append(f"**▶️ Tocando:** {e.musica_atual['titulo'][:60]}")
    for i, m in enumerate(e.fila, 1):
        linhas.append(f"`{i}.` {m['titulo'][:55]}")
    await ctx.send("\n".join(linhas)[:2000])

@bot.command(name="remover", aliases=["remove", "rm"])
async def cmd_remover(ctx, posicao: int = None):
    e = estado(ctx.guild.id)
    if posicao is None or posicao < 1 or posicao > len(e.fila):
        await ctx.send(f"❌ Posição válida: 1–{len(e.fila)}")
        return
    lista = list(e.fila)
    removida = lista.pop(posicao - 1)
    e.fila = deque(lista)
    await ctx.send(f"🗑️ Removido: **{removida['titulo'][:60]}**")

@bot.command(name="limpar", aliases=["clear"])
async def cmd_limpar(ctx):
    estado(ctx.guild.id).fila.clear()
    await ctx.send("🧹 Fila limpa!")

@bot.command(name="parar", aliases=["stop"])
async def cmd_parar(ctx):
    e = estado(ctx.guild.id)
    e.fila.clear()
    e.modo_loop = False
    e.musica_atual = None
    vc = ctx.voice_client
    if vc:
        vc.stop()
        await vc.disconnect()
    await ctx.send("⏹️ Parado.")

@bot.command(name="tocando", aliases=["np", "nowplaying"])
async def cmd_tocando(ctx):
    e = estado(ctx.guild.id)
    if e.musica_atual:
        await ctx.send(embed=embed_musica(e.musica_atual))
    else:
        await ctx.send("❌ Nada tocando.")

@bot.command(name="painel")
async def cmd_painel(ctx):
    e = estado(ctx.guild.id)
    embed = discord.Embed(title="🎵 Painel de Controle", color=0xFF0000)
    embed.add_field(
        name="🎧 Tocando",
        value=e.musica_atual['titulo'][:100] if e.musica_atual else "Nada",
        inline=False
    )
    if e.fila:
        txt = "\n".join(f"`{i}.` {m['titulo'][:45]}" for i, m in enumerate(e.fila, 1))
        embed.add_field(name="📋 Fila", value=txt[:1024], inline=False)
    embed.add_field(name="🔁 Loop", value="ON" if e.modo_loop else "OFF", inline=True)
    embed.add_field(name="🔊 Volume", value=f"{int(e.volume * 100)}%", inline=True)
    await ctx.send(embed=embed, view=Painel(ctx.guild.id))

@bot.command(name="comandos", aliases=["ajuda"])
async def cmd_comandos(ctx):
    embed = discord.Embed(title="🎵 Comandos do Bot", color=0xFF0000)
    embed.add_field(
        name="🎧 Música",
        value="`!yt <nome/link>` `!pause` `!continuar` `!skip` `!parar` `!tocando`",
        inline=False
    )
    embed.add_field(
        name="📋 Fila",
        value="`!fila` `!remover <n>` `!limpar`",
        inline=False
    )
    embed.add_field(
        name="⚙️ Controles",
        value="`!painel` `!loop` `!volume <0-100>`",
        inline=False
    )
    embed.set_footer(text="!play e !tocar também funcionam como !yt")
    await ctx.send(embed=embed)

# ========== EVENTOS ==========
@bot.event
async def on_ready():
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.listening, name="!yt")
    )
    if COOKIES_PATH:
        print(f"✅ cookies.txt: {COOKIES_PATH} ({os.path.getsize(COOKIES_PATH)} bytes)")
    else:
        print("⚠️  Sem cookies.txt")
    print(f"✅ {bot.user} online!")

@bot.event
async def on_voice_state_update(member, before, after):
    if member == bot.user:
        return
    vc = member.guild.voice_client
    if vc and len(vc.channel.members) == 1:
        e = estado(member.guild.id)
        e.fila.clear()
        e.musica_atual = None
        await vc.disconnect()

# ========== INICIAR ==========
bot.run(TOKEN)
