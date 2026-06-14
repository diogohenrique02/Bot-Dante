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
from collections import deque

# ========== CONFIGURAÇÕES ==========
TOKEN = os.getenv("TOKEN", "")
PREFIXO = "!"
TEMP_DIR = "/tmp/dante_audio"
os.makedirs(TEMP_DIR, exist_ok=True)

# Caminho do cookies.txt
COOKIES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
if not os.path.exists(COOKIES_PATH):
    print(f"⚠️  cookies.txt não encontrado em {COOKIES_PATH}")
    COOKIES_PATH = None

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
        # Cliente Android nao eh bloqueado por IP como o cliente web
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'web'],
                'player_skip': ['webpage', 'configs'],
            }
        },
        'http_headers': {
            'User-Agent': (
                'com.google.android.youtube/19.09.37 '
                '(Linux; U; Android 11) gzip'
            )
        },
    }
    if COOKIES_PATH:
        opts['cookiefile'] = COOKIES_PATH
    return opts

def buscar_info(busca: str) -> dict:
    """Busca informações sem baixar."""
    opts = _ydl_base()

    if any(x in busca for x in ('youtube.com', 'youtu.be')):
        query = busca
    else:
        query = f"ytsearch3:{busca}"  # pede 3 resultados para ter fallback

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(query, download=False)

        # Se veio lista de resultados, pega o primeiro válido
        if 'entries' in info:
            entries = [e for e in info['entries'] if e and e.get('id')]
            if not entries:
                raise Exception("Nenhum resultado encontrado.")
            info = entries[0]

        if not info or not info.get('id'):
            raise Exception("Resultado inválido do YouTube.")

        return {
            'webpage_url': info.get('webpage_url', info.get('url', '')),
            'titulo': info.get('title', 'Desconhecido'),
            'artista': info.get('uploader', 'Desconhecido'),
            'thumb': info.get('thumbnail', ''),
            'duracao': int(info.get('duration') or 0),
            'id': info.get('id', ''),
        }

def baixar_audio(webpage_url: str, video_id: str) -> str:
    """Baixa o áudio para arquivo .mp3 e retorna o caminho."""
    nome = hashlib.md5(video_id.encode()).hexdigest()[:12]
    caminho = os.path.join(TEMP_DIR, nome)
    caminho_mp3 = caminho + ".mp3"

    # Já tem no cache
    if os.path.exists(caminho_mp3) and os.path.getsize(caminho_mp3) > 10000:
        return caminho_mp3

    opts = _ydl_base()
    opts.update({
        # Aceita qualquer formato com áudio — o FFmpeg converte pra mp3
        'format': 'bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio/best[acodec!=none]/best',
        'outtmpl': caminho,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '128',
        }],
        'ffmpeg_location': os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe()),
        # Ignora erros de formato e tenta o próximo disponível
        'ignoreerrors': False,
        'retries': 3,
    })

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([webpage_url])

    # yt-dlp pode salvar como .mp3 ou sem extensão
    if not os.path.exists(caminho_mp3):
        for f in os.listdir(TEMP_DIR):
            if f.startswith(nome):
                os.rename(os.path.join(TEMP_DIR, f), caminho_mp3)
                break

    return caminho_mp3

def limpar_cache():
    """Remove arquivos mais antigos se o cache passar de 500MB."""
    try:
        arquivos = [
            (os.path.join(TEMP_DIR, f), os.path.getmtime(os.path.join(TEMP_DIR, f)))
            for f in os.listdir(TEMP_DIR)
        ]
        total = sum(os.path.getsize(a) for a, _ in arquivos)
        if total > 500 * 1024 * 1024:  # 500MB
            arquivos.sort(key=lambda x: x[1])
            for caminho, _ in arquivos[:len(arquivos)//2]:
                os.remove(caminho)
    except:
        pass

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
        await msg.edit(content=f"❌ Não encontrei: {str(ex)[:100]}")
        return

    await msg.edit(content=f"⏳ Baixando áudio de **{info['titulo'][:60]}**...")

    try:
        caminho = await asyncio.to_thread(baixar_audio, info['webpage_url'], info['id'])
        info['caminho_audio'] = caminho
        limpar_cache()
    except Exception as ex:
        await msg.edit(content=f"❌ Erro ao baixar: {str(ex)[:100]}")
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
