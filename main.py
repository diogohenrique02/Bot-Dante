import discord
from discord.ext import commands
from discord.ui import View
from flask import Flask
from threading import Thread
import yt_dlp
import asyncio
import os
from collections import deque

# ========== CONFIGURAÇÕES ==========
TOKEN = os.getenv("TOKEN", "")
PREFIXO = "!"

# ─── Caminho do cookies.txt ───────────────────────────────────────────────────
# Coloque o cookies.txt na raiz do projeto (junto com main.py)
COOKIES_PATH = os.path.join(os.path.dirname(__file__), "cookies.txt")
if not os.path.exists(COOKIES_PATH):
    print(f"⚠️  AVISO: cookies.txt não encontrado em {COOKIES_PATH}")
    COOKIES_PATH = None

# ========== SERVIDOR WEB (UptimeRobot / Render) ==========
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
bot = commands.Bot(command_prefix=PREFIXO, intents=intents)

# ========== ESTADO POR SERVIDOR ==========
# Cada guild_id tem seu próprio estado para suportar múltiplos servidores
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
def _ydl_opts(extra: dict = {}) -> dict:
    """Monta as opções do yt-dlp sempre com cookies se disponível."""
    base = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'source_address': '0.0.0.0',
        'force_ipv4': True,
        'http_headers': {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            )
        },
    }
    # ✅ CORREÇÃO PRINCIPAL: passa o cookies.txt para o yt-dlp
    if COOKIES_PATH:
        base['cookiefile'] = COOKIES_PATH

    base.update(extra)
    return base

FFMPEG_OPTIONS = {
    'before_options': (
        '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 '
        '-reconnect_on_network_error 1'
    ),
    'options': '-vn -bufsize 64k',
}

def _extrair_info(query: str) -> dict:
    """Extrai informações de áudio. Levanta exceção se não encontrar."""
    with yt_dlp.YoutubeDL(_ydl_opts()) as ydl:
        info = ydl.extract_info(query, download=False)
        if 'entries' in info:
            info = info['entries'][0]

        # Pega a URL do stream de áudio
        url_audio = None
        formatos = info.get('formats', [])
        for f in reversed(formatos):
            if f.get('acodec') != 'none' and f.get('vcodec') == 'none':
                url_audio = f.get('url')
                break
        if not url_audio:
            url_audio = info.get('url', '')

        return {
            'url': url_audio,
            'webpage_url': info.get('webpage_url', ''),
            'titulo': info.get('title', 'Desconhecido'),
            'artista': info.get('uploader', 'Desconhecido'),
            'thumb': info.get('thumbnail', ''),
            'duracao': int(info.get('duration') or 0),
            'fonte': 'YouTube' if 'youtube' in info.get('extractor', '') else info.get('extractor', '?').title(),
        }

def buscar_musica(busca: str) -> dict:
    """Resolve a busca do usuário para informações de áudio."""
    # Link direto do YouTube ou SoundCloud
    if any(x in busca for x in ('youtube.com', 'youtu.be', 'soundcloud.com')):
        return _extrair_info(busca)

    # Busca por texto — tenta YouTube primeiro
    try:
        return _extrair_info(f"ytsearch1:{busca}")
    except Exception:
        pass

    # Fallback: SoundCloud
    try:
        return _extrair_info(f"scsearch1:{busca}")
    except Exception:
        raise Exception("Não encontrei a música em nenhuma plataforma.")

# ========== PLAYER ==========
async def tocar_proximo(vc: discord.VoiceClient, guild_id: int):
    """Toca a próxima música da fila (ou repete em modo loop)."""
    e = estado(guild_id)

    if not vc or not vc.is_connected():
        return

    # Modo loop: repete a música atual
    if e.modo_loop and e.musica_atual:
        info = e.musica_atual
    elif e.fila:
        info = e.fila.popleft()
        e.musica_atual = info
    else:
        e.musica_atual = None
        return

    try:
        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(info['url'], **FFMPEG_OPTIONS),
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
        # Tenta a próxima mesmo em caso de erro
        asyncio.run_coroutine_threadsafe(tocar_proximo(vc, guild_id), bot.loop)

# ========== PAINEL DE BOTÕES ==========
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
        await interaction.response.send_message("⏹️ Parado e saí do canal.", ephemeral=True)

# ========== HELPERS ==========
def formatar_duracao(segundos: int) -> str:
    m, s = divmod(segundos, 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}min" if h else f"{m}:{s:02d}"

def embed_musica(info: dict, titulo_embed="🎧 Tocando agora") -> discord.Embed:
    embed = discord.Embed(
        title=titulo_embed,
        description=f"**{info['titulo'][:100]}**",
        color=0xFF0000
    )
    if info.get('thumb'):
        embed.set_thumbnail(url=info['thumb'])
    embed.add_field(name="👤 Artista", value=info['artista'][:50], inline=True)
    embed.add_field(name="🌐 Fonte", value=info.get('fonte', '?'), inline=True)
    if info.get('duracao', 0) > 0:
        embed.add_field(name="⏱️ Duração", value=formatar_duracao(info['duracao']), inline=True)
    return embed

async def conectar_vc(ctx) -> discord.VoiceClient | None:
    """Garante conexão com o canal de voz do usuário."""
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
    """!yt <nome ou link> — busca e toca uma música"""
    if not busca:
        await ctx.send("❌ Use: `!yt nome da música ou link`")
        return

    vc = await conectar_vc(ctx)
    if not vc:
        return

    msg = await ctx.send(f"🔎 Buscando: `{busca}`...")

    try:
        info = await asyncio.to_thread(buscar_musica, busca)
    except Exception as ex:
        await msg.edit(content=f"❌ Erro: {str(ex)[:120]}")
        return

    if not info.get('url'):
        await msg.edit(content="❌ Não consegui obter o áudio.")
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
                discord.FFmpegPCMAudio(info['url'], **FFMPEG_OPTIONS),
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
    """!skip — pula a música atual"""
    vc = ctx.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
        await ctx.send("⏭️ Pulando...")
    else:
        await ctx.send("❌ Nada tocando.")

@bot.command(name="pause", aliases=["pausar"])
async def cmd_pause(ctx):
    """!pause — pausa"""
    vc = ctx.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await ctx.send("⏸️ Pausado.")
    else:
        await ctx.send("❌ Nada tocando.")

@bot.command(name="continuar", aliases=["resume"])
async def cmd_continuar(ctx):
    """!continuar — retoma"""
    vc = ctx.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await ctx.send("▶️ Retomado.")
    else:
        await ctx.send("❌ Não está pausado.")

@bot.command(name="loop")
async def cmd_loop(ctx):
    """!loop — ativa/desativa repetição"""
    e = estado(ctx.guild.id)
    e.modo_loop = not e.modo_loop
    await ctx.send(f"🔁 Loop {'**ativado**' if e.modo_loop else '**desativado**'}")

@bot.command(name="volume", aliases=["vol"])
async def cmd_volume(ctx, vol: int = None):
    """!volume [0-100] — ajusta o volume"""
    e = estado(ctx.guild.id)
    if vol is None:
        await ctx.send(f"🔊 Volume atual: **{int(e.volume * 100)}%**")
        return
    if not 0 <= vol <= 100:
        await ctx.send("❌ Use um valor entre 0 e 100.")
        return
    e.volume = vol / 100
    vc = ctx.voice_client
    if vc and vc.source and isinstance(vc.source, discord.PCMVolumeTransformer):
        vc.source.volume = e.volume
    await ctx.send(f"🔊 Volume: **{vol}%**")

@bot.command(name="fila", aliases=["queue", "q"])
async def cmd_fila(ctx):
    """!fila — mostra a fila de músicas"""
    e = estado(ctx.guild.id)
    if not e.fila and not e.musica_atual:
        await ctx.send("📋 A fila está vazia.")
        return
    linhas = []
    if e.musica_atual:
        linhas.append(f"**▶️ Tocando:** {e.musica_atual['titulo'][:60]}")
    for i, m in enumerate(e.fila, 1):
        linhas.append(f"`{i}.` {m['titulo'][:55]}")
    await ctx.send("\n".join(linhas)[:2000])

@bot.command(name="remover", aliases=["remove", "rm"])
async def cmd_remover(ctx, posicao: int = None):
    """!remover <n> — remove da fila pela posição"""
    e = estado(ctx.guild.id)
    if posicao is None or posicao < 1 or posicao > len(e.fila):
        await ctx.send(f"❌ Informe uma posição válida (1–{len(e.fila)}).")
        return
    lista = list(e.fila)
    removida = lista.pop(posicao - 1)
    e.fila = deque(lista)
    await ctx.send(f"🗑️ Removido: **{removida['titulo'][:60]}**")

@bot.command(name="limpar", aliases=["clear"])
async def cmd_limpar(ctx):
    """!limpar — limpa a fila"""
    e = estado(ctx.guild.id)
    e.fila.clear()
    await ctx.send("🧹 Fila limpa!")

@bot.command(name="parar", aliases=["stop"])
async def cmd_parar(ctx):
    """!parar — para tudo e sai do canal"""
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
    """!tocando — mostra a música atual"""
    e = estado(ctx.guild.id)
    if e.musica_atual:
        await ctx.send(embed=embed_musica(e.musica_atual))
    else:
        await ctx.send("❌ Nada tocando agora.")

@bot.command(name="painel")
async def cmd_painel(ctx):
    """!painel — painel interativo"""
    e = estado(ctx.guild.id)
    embed = discord.Embed(title="🎵 Painel de Controle", color=0xFF0000)
    if e.musica_atual:
        embed.add_field(name="🎧 Tocando", value=e.musica_atual['titulo'][:100], inline=False)
    else:
        embed.add_field(name="🎧 Tocando", value="Nada", inline=False)
    if e.fila:
        txt = "\n".join(f"`{i}.` {m['titulo'][:45]}" for i, m in enumerate(e.fila, 1))
        embed.add_field(name="📋 Fila", value=txt[:1024], inline=False)
    embed.add_field(name="🔁 Loop", value="ON" if e.modo_loop else "OFF", inline=True)
    embed.add_field(name="🔊 Volume", value=f"{int(e.volume * 100)}%", inline=True)
    await ctx.send(embed=embed, view=Painel(ctx.guild.id))

@bot.command(name="comandos", aliases=["help", "ajuda"])
async def cmd_comandos(ctx):
    """!comandos — lista de comandos"""
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
    embed.set_footer(text="Aliases: !play e !tocar também funcionam como !yt")
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
    """Desconecta automaticamente se ficar sozinho no canal."""
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
