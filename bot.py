from datetime import datetime, time
from http.server import HTTPServer, BaseHTTPRequestHandler
import os
import sqlite3
import threading
import discord
from discord import app_commands
from discord.ext import commands, tasks

# ==========================================
# EINSTELLUNGEN FÜR ARMY / MILITÄR
# ==========================================
DIENST_KANAL_ID = 1562632780297277450  # ERSETZE DIES DURCH DEINE KANAL-ID!
BEWERBUNG_LOG_KANAL_ID = 1562630883587133450  # KANAL FÜR DIE ADMINGESICHTETEN BEWERBUNGEN!
TICKET_LOG_KANAL_ID = 1562630985303064707  # KANAL FÜR TICKET-BENACHRICHTIGUNGEN!
AKTEN_ARCHIV_KANAL_ID = (
    1562846902469533768  # KANAL FÜR DAS AKTENARCHIV (z. B. #personalakten)!
)
ZIMMER_KANAL_ID = (
    1562649383189880873  # KANAL FÜR DEN LIVE-ZIMMERPLAN (z. B. #zimmeraufteilung)!
)

ROLLE_ARMY = "Army"
ROLLE_BESUCHER = "Besucher"
ROLLE_AUSBILDER = "Ausbilder"
ROLLE_TICKETBEARBEITER = (
    "Ticketbearbeiter"  # Rolle für die Ticket-Bearbeitung!
)
UHRZEIT_POST = time(hour=15, minute=0)


# --- DATENBANK EINRICHTUNG ---
def init_db():
  conn = sqlite3.connect("army.db")
  cursor = conn.cursor()
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS items (
            name TEXT PRIMARY KEY,
            quantity INTEGER,
            min_quantity INTEGER
        )
    """)
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            action TEXT,
            details TEXT,
            user TEXT
        )
    """)
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS abmeldungen (
            user_id TEXT PRIMARY KEY,
            username TEXT,
            zeitraum TEXT,
            grund TEXT
        )
    """)
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS sold_kasse (
            user_id TEXT PRIMARY KEY,
            username TEXT,
            status TEXT
        )
    """)
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS einstellungen (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS termine (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            titel TEXT,
            datum_uhrzeit TEXT,
            beschreibung TEXT,
            erstellt_von TEXT
        )
    """)
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS personalakten (
            user_id TEXT PRIMARY KEY,
            username TEXT,
            dienstrang TEXT,
            dienstnummer TEXT,
            eintraege TEXT,
            auszeichnungen TEXT,
            message_id INTEGER
        )
    """)
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS zimmerbelegung (
            bett_id TEXT PRIMARY KEY,
            haus INTEGER,
            etage TEXT,
            raum TEXT,
            seite TEXT,
            user_id TEXT,
            username TEXT
        )
    """)
  cursor.execute(
      "INSERT OR IGNORE INTO einstellungen (key, value) VALUES ('sold_betrag',"
      " '50000')"
  )
  conn.commit()
  conn.close()


init_db()


def log_action(action: str, details: str, user: str):
  timestamp = datetime.now().strftime("%d.%m.%Y %H:%M")
  conn = sqlite3.connect("army.db")
  cursor = conn.cursor()
  cursor.execute(
      "INSERT INTO logs (timestamp, action, details, user) VALUES (?, ?, ?, ?)",
      (timestamp, action, details, user),
  )
  conn.commit()
  conn.close()

  global live_log_message
  if live_log_message:
    try:
      bot.loop.create_task(live_log_message.edit(embed=get_log_embed()))  # type: ignore[attr-defined]
    except Exception:
      pass


def get_einstellung(key):
  conn = sqlite3.connect("army.db")
  cursor = conn.cursor()
  cursor.execute("SELECT value FROM einstellungen WHERE key = ?", (key,))
  row = cursor.fetchone()
  conn.close()
  return row[0] if row else None


# --- EMBED HELFER (KOMPAKTE ZIMMERANZEIGE) ---
def get_zimmer_embeds():
  conn = sqlite3.connect("army.db")
  cursor = conn.cursor()
  cursor.execute(
      "SELECT bett_id, username FROM zimmerbelegung WHERE username IS NOT NULL"
      " AND username != ''"
  )
  rows = cursor.fetchall()
  conn.close()

  belegung = {r[0]: r[1] for r in rows}
  embeds = []

  for h in range(1, 5):
    embed = discord.Embed(
        title=f"🏠 Zimmeraufteilung Haus {h}",
        color=discord.Color.from_rgb(52, 152, 219),
    )

    for etage_code, etage_name in [
        ("UG", "Untergeschoss (UG)"),
        ("OG", "Obergeschoss (OG)"),
    ]:
      desc = ""
      for r_num in [1, 2, 3, 4]:
        for r_let in ["A", "B"]:
          raum_name = f"Room {r_num}{r_let}"
          bett_rechts_id = f"H{h}_{etage_code}_{raum_name}_Rechts"
          bett_links_id = f"H{h}_{etage_code}_{raum_name}_Links"

          user_r = belegung.get(bett_rechts_id, "—")
          user_l = belegung.get(bett_links_id, "—")

          # Kompaktes Format in einer Zeile
          desc += f"`{raum_name}` **R:** {user_r} | **L:** {user_l}\n"

      embed.add_field(name=f"📌 {etage_name}", value=desc, inline=False)

    embed.set_footer(
        text=f"Stand: {datetime.now().strftime('%d.%m.%Y %H:%M')} Uhr"
    )
    embeds.append(embed)

  return embeds


live_zimmer_messages = []


async def update_live_zimmerplan(guild):
  global live_zimmer_messages
  if not live_zimmer_messages:
    return

  embeds = get_zimmer_embeds()
  try:
    for idx, msg in enumerate(live_zimmer_messages):
      if idx < len(embeds):
        await msg.edit(embed=embeds[idx])
  except Exception:
    pass


def build_akte_embed(
    target_user: discord.Member,
    rang: str,
    nummer: str,
    eintraege: str,
    auszeichnungen: str,
    bearbeiter_name: str,
):
  embed = discord.Embed(
      title=f"🎖️ Personalakte: {target_user.display_name}",
      color=discord.Color.from_rgb(44, 62, 80),
  )
  embed.set_thumbnail(
      url=(
          target_user.avatar.url
          if target_user.avatar
          else target_user.default_avatar.url
      )
  )
  embed.add_field(name="👤 Mitglied", value=target_user.mention, inline=True)
  embed.add_field(
      name="🪖 Dienstrang", value=rang if rang else "Unbekannt", inline=True
  )
  embed.add_field(
      name="🔢 Dienstnummer",
      value=f"#{nummer}" if nummer else "N/A",
      inline=True,
  )
  embed.add_field(
      name="📜 Notizen / Einträge / Vermerke",
      value=eintraege if eintraege else "Keine Einträge vorhanden.",
      inline=False,
  )
  embed.add_field(
      name="🏅 Auszeichnungen & Lehrgänge",
      value=auszeichnungen if auszeichnungen else "Keine Auszeichnungen.",
      inline=False,
  )
  embed.set_footer(
      text=(
          f"Zuletzt bearbeitet von {bearbeiter_name} •"
          f" {datetime.now().strftime('%d.%m.%Y %H:%M')} Uhr"
      )
  )
  return embed


def get_bestand_embed():
  conn = sqlite3.connect("army.db")
  cursor = conn.cursor()
  cursor.execute("SELECT name, quantity, min_quantity FROM items")
  rows = cursor.fetchall()
  conn.close()

  embed = discord.Embed(
      title="🪖 Waffenkammer / Armory Bestand",
      description=(
          "Aktueller Bestand an Waffen, Munition & Ausrüstung.\n"
          "Aktualisiert sich automatisch bei jeder Entnahme/Einlagerung.\n\n"
      ),
      color=discord.Color.from_rgb(46, 125, 50),
  )

  if not rows:
    embed.description += "Die Waffenkammer ist aktuell leer."
  else:
    desc = ""
    for name, qty, min_qty in rows:
      warning = " ⚠️ (KRITISCHER BESTAND!)" if qty < min_qty else ""
      desc += f"• **{name}**: **{qty} Stk.**{warning}\n"
    embed.description += desc

  embed.set_footer(
      text=f"Stand: {datetime.now().strftime('%d.%m.%Y %H:%M')} Uhr"
  )
  return embed


def get_abmeldungen_embed():
  conn = sqlite3.connect("army.db")
  cursor = conn.cursor()
  cursor.execute("SELECT username, zeitraum, grund FROM abmeldungen")
  rows = cursor.fetchall()
  conn.close()

  embed = discord.Embed(
      title="📋 Dienstabmeldungen & Urlaub",
      color=discord.Color.from_rgb(41, 128, 185),
  )

  if not rows:
    embed.description = "Aktuell befinden sich alle Soldaten im Dienst."
  else:
    desc = ""
    for username, zeitraum, grund in rows:
      desc += f"• **{username}** | Zeitraum: *{zeitraum}* | Grund: *{grund}*\n"
    embed.description = desc

  embed.set_footer(text="HQ Personalabteilung • Zeitzone: Europe/Berlin")
  return embed


def get_sold_embed():
  betrag_str = get_einstellung("sold_betrag") or "50000"
  try:
    betrag_int = int(betrag_str)
  except ValueError:
    betrag_int = 50000

  conn = sqlite3.connect("army.db")
  cursor = conn.cursor()
  cursor.execute("SELECT username, status FROM sold_kasse")
  rows = cursor.fetchall()
  conn.close()

  bezahlt = [r[0] for r in rows if r[1] == "bezahlt"]
  offen = [r[0] for r in rows if r[1] == "offen"]
  befreit = [r[0] for r in rows if r[1] == "befreit"]
  gesamtsumme = len(bezahlt) * betrag_int

  embed = discord.Embed(
      title="💵 Army Kassenbeitrag / Soldabgabe",
      description=(
          f"**Pflichtbeitrag:** {betrag_int:,}$ pro Soldat\n"
          f"**Eingezahlt:** {gesamtsumme:,}$\n"
          "Bitte begleicht eure Abgabe rechtzeitig beim Führungspersonal!"
      ),
      color=discord.Color.from_rgb(241, 196, 15),
  )

  bezahlt_str = (
      "\n".join([f"• **{u}**" for u in bezahlt]) if bezahlt else "Keine"
  )
  offen_str = "\n".join([f"• **{u}**" for u in offen]) if offen else "Keine"
  befreit_str = "\n".join([f"• **{u}**" for u in befreit]) if befreit else "Keine"

  embed.add_field(
      name=f"✅ Bezahlt ({len(bezahlt)})", value=bezahlt_str, inline=False
  )
  embed.add_field(name=f"❌ Ausstehend ({len(offen)})", value=offen_str, inline=False)
  embed.add_field(
      name=f"🟦 Befreit ({len(befreit)})", value=befreit_str, inline=False
  )

  embed.set_footer(text="Hauptquartier Finanzstelle")
  return embed


def get_log_embed():
  conn = sqlite3.connect("army.db")
  cursor = conn.cursor()
  cursor.execute(
      "SELECT timestamp, action, details, user FROM logs ORDER BY id DESC LIMIT"
      " 15"
  )
  rows = cursor.fetchall()
  conn.close()

  embed = discord.Embed(
      title="📜 Militärischer Dienst-Aktivitäts-Log",
      description="Protokollierung aller Aktionen & Ausrüstungsbewegungen.\n\n",
      color=discord.Color.from_rgb(127, 140, 141),
  )

  if not rows:
    embed.description += "Keine Einträge vorhanden."
  else:
    desc = ""
    for r in rows:
      desc += f"[`{r[0]}`] **{r[1]}** | {r[2]} — *{r[3]}*\n"
    embed.description += desc

  embed.set_footer(
      text=f"Letzte Aktualisierung: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
  )
  return embed


def get_kalender_embed():
  conn = sqlite3.connect("army.db")
  cursor = conn.cursor()
  cursor.execute(
      "SELECT id, titel, datum_uhrzeit, beschreibung FROM termine ORDER BY id"
      " ASC"
  )
  rows = cursor.fetchall()
  conn.close()

  embed = discord.Embed(
      title="🪖 Dienstplan, Appelle & Manöver",
      description="Anstehende Termine, Ausbildungen und Einsätze:\n\n",
      color=discord.Color.from_rgb(52, 152, 219),
  )

  if not rows:
    embed.description += "Aktuell sind keine Einsätze oder Übungen terminiert."
  else:
    for t_id, titel, datum_uhrzeit, beschreibung in rows:
      embed.add_field(
          name=f"📌 Einsatz ID #{t_id} | {titel}",
          value=(
              f"🗓️ **Zeitpunkt:** {datum_uhrzeit}\n📝 **Befehl / Info:**"
              f" {beschreibung}"
          ),
          inline=False,
      )

  embed.set_footer(
      text=f"Stand: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
  )
  return embed


live_bestand_message = None
live_abmeldung_message = None
live_sold_message = None
live_log_message = None
live_kalender_message = None


# --- ZIMMERAUFTEILUNG VIEWS ---
class ZimmerZuweisenView(discord.ui.View):

  def __init__(self):
    super().__init__(timeout=120)
    self.selected_user = None
    self.selected_haus = None
    self.selected_etage = None
    self.selected_raum = None
    self.selected_seite = None

  @discord.ui.select(
      cls=discord.ui.UserSelect,
      placeholder="1. Wähle das Mitglied aus...",
      row=0,
  )
  async def select_user(
      self, interaction: discord.Interaction, select: discord.ui.UserSelect
  ):
    self.selected_user = select.values[0]
    await interaction.response.send_message(
        f"👤 Gewählt: **{self.selected_user.display_name}**. Wähle nun Haus,"
        " Etage, Raum und Seite aus:",
        ephemeral=True,
    )

  @discord.ui.select(
      placeholder="2. Haus wählen...",
      options=[
          discord.SelectOption(label="Haus 1", value="1", emoji="🏠"),
          discord.SelectOption(label="Haus 2", value="2", emoji="🏠"),
          discord.SelectOption(label="Haus 3", value="3", emoji="🏠"),
          discord.SelectOption(label="Haus 4", value="4", emoji="🏠"),
      ],
      row=1,
  )
  async def select_haus(
      self, interaction: discord.Interaction, select: discord.ui.Select
  ):
    self.selected_haus = int(select.values[0])
    await interaction.response.defer(ephemeral=True)

  @discord.ui.select(
      placeholder="3. Etage wählen...",
      options=[
          discord.SelectOption(
              label="Untergeschoss (UG)", value="UG", emoji="⬇️"
          ),
          discord.SelectOption(
              label="Obergeschoss (OG)", value="OG", emoji="⬆️"
          ),
      ],
      row=2,
  )
  async def select_etage(
      self, interaction: discord.Interaction, select: discord.ui.Select
  ):
    self.selected_etage = select.values[0]
    await interaction.response.defer(ephemeral=True)

  @discord.ui.select(
      placeholder="4. Raum wählen...",
      options=[
          discord.SelectOption(label="Room 1A", value="Room 1A"),
          discord.SelectOption(label="Room 1B", value="Room 1B"),
          discord.SelectOption(label="Room 2A", value="Room 2A"),
          discord.SelectOption(label="Room 2B", value="Room 2B"),
          discord.SelectOption(label="Room 3A", value="Room 3A"),
          discord.SelectOption(label="Room 3B", value="Room 3B"),
          discord.SelectOption(label="Room 4A", value="Room 4A"),
          discord.SelectOption(label="Room 4B", value="Room 4B"),
      ],
      row=3,
  )
  async def select_raum(
      self, interaction: discord.Interaction, select: discord.ui.Select
  ):
    self.selected_raum = select.values[0]
    await interaction.response.defer(ephemeral=True)

  @discord.ui.select(
      placeholder="5. Bettseite wählen...",
      options=[
          discord.SelectOption(label="Rechts", value="Rechts", emoji="👉"),
          discord.SelectOption(label="Links", value="Links", emoji="👈"),
      ],
      row=4,
  )
  async def select_seite(
      self, interaction: discord.Interaction, select: discord.ui.Select
  ):
    self.selected_seite = select.values[0]

    if (
        not self.selected_user
        or not self.selected_haus
        or not self.selected_etage
        or not self.selected_raum
    ):
      await interaction.response.send_message(
          "❌ Bitte wähle zuerst alle Felder (User, Haus, Etage, Raum und"
          " Seite) aus!",
          ephemeral=True,
      )
      return

    bett_id = f"H{self.selected_haus}_{self.selected_etage}_{self.selected_raum}_{self.selected_seite}"
    user_id = str(self.selected_user.id)
    username = self.selected_user.display_name

    conn = sqlite3.connect("army.db")
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM zimmerbelegung WHERE user_id = ?", (user_id,)
    )  # Altes Bett räumen
    cursor.execute(
        """
        REPLACE INTO zimmerbelegung (bett_id, haus, etage, raum, seite, user_id, username)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """,
        (
            bett_id,
            self.selected_haus,
            self.selected_etage,
            self.selected_raum,
            self.selected_seite,
            user_id,
            username,
        ),
    )
    conn.commit()
    conn.close()

    log_action(
        "Zimmereinteilung",
        f"{username} -> Haus {self.selected_haus} ({self.selected_etage},"
        f" {self.selected_raum}, Bett {self.selected_seite})",
        interaction.user.display_name,
    )

    await update_live_zimmerplan(interaction.guild)

    await interaction.response.send_message(
        f"✅ **{username}** wurde in **Haus {self.selected_haus}**"
        f" ({self.selected_etage}, {self.selected_raum}, Bett"
        f" {self.selected_seite}) eingetragen!",
        ephemeral=True,
    )


class ZimmerRaeumenView(discord.ui.View):

  def __init__(self):
    super().__init__(timeout=60)

  @discord.ui.select(
      cls=discord.ui.UserSelect, placeholder="Wähle das Mitglied aus..."
  )
  async def select_user(
      self, interaction: discord.Interaction, select: discord.ui.UserSelect
  ):
    target_user = select.values[0]
    user_id = str(target_user.id)

    conn = sqlite3.connect("army.db")
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM zimmerbelegung WHERE user_id = ?", (user_id,)
    )
    deleted = cursor.rowcount
    conn.commit()
    conn.close()

    if deleted > 0:
      log_action(
          "Bett geräumt",
          f"Bett von {target_user.display_name} freigegeben",
          interaction.user.display_name,
      )
      await update_live_zimmerplan(interaction.guild)
      await interaction.response.send_message(
          f"✅ Das Bett von **{target_user.display_name}** wurde freigegeben!",
          ephemeral=True,
      )
    else:
      await interaction.response.send_message(
          f"❌ **{target_user.display_name}** war keinem Bett zugewiesen.",
          ephemeral=True,
      )


class ZimmerPanelView(discord.ui.View):

  def __init__(self):
    super().__init__(timeout=None)

  @discord.ui.button(
      label="Bett zuweisen",
      style=discord.ButtonStyle.green,
      emoji="🛏️",
      custom_id="btn_zimmer_zuweisen",
  )
  async def zuweisen(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    await interaction.response.send_message(
        "Wähle die Daten aus, um ein Bett zuzuweisen:",
        view=ZimmerZuweisenView(),
        ephemeral=True,
    )

  @discord.ui.button(
      label="Bett räumen",
      style=discord.ButtonStyle.red,
      emoji="🚪",
      custom_id="btn_zimmer_raeumen",
  )
  async def raeumen(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    await interaction.response.send_message(
        "Wähle das Mitglied aus, das aus seinem Zimmer ausziehen soll:",
        view=ZimmerRaeumenView(),
        ephemeral=True,
    )

  @discord.ui.button(
      label="Übersicht anzeigen",
      style=discord.ButtonStyle.primary,
      emoji="📋",
      custom_id="btn_zimmer_anzeigen",
  )
  async def anzeigen(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    embeds = get_zimmer_embeds()
    await interaction.response.send_message(embeds=embeds, ephemeral=True)


# --- PERSONALAKTEN MODAL & VIEWS ---
class AkteBearbeitenModal(discord.ui.Modal):

  def __init__(
      self,
      target_user: discord.Member,
      rang="",
      nummer="",
      eintraege="",
      auszeichnungen="",
  ):
    super().__init__(title=f"Akte: {target_user.display_name[:20]}")
    self.target_user = target_user

    self.rang_input = discord.ui.TextInput(
        label="Dienstrang",
        placeholder="z.B. Private (PVT) / Corporal (CPL)",
        default=rang,
        required=False,
    )
    self.nummer_input = discord.ui.TextInput(
        label="Dienstnummer",
        placeholder="z.B. 402",
        default=nummer,
        required=False,
    )
    self.eintraege_input = discord.ui.TextInput(
        label="Einträge / Vermerke / Notizen",
        style=discord.TextStyle.paragraph,
        placeholder="Beförderungen, Abmahnungen oder allgemeine Notizen...",
        default=eintraege,
        required=False,
    )
    self.auszeichnung_input = discord.ui.TextInput(
        label="Auszeichnungen / Lehrgänge",
        style=discord.TextStyle.paragraph,
        placeholder="z.B. Scharfschützen-Ausbildung, Verdienstmedaille...",
        default=auszeichnungen,
        required=False,
    )

    self.add_item(self.rang_input)
    self.add_item(self.nummer_input)
    self.add_item(self.eintraege_input)
    self.add_item(self.auszeichnung_input)

  async def on_submit(self, interaction: discord.Interaction):
    guild = interaction.guild
    user_id = str(self.target_user.id)
    username = self.target_user.display_name

    rang = self.rang_input.value.strip()
    nummer = self.nummer_input.value.strip()
    eintraege = self.eintraege_input.value.strip()
    auszeichnung = self.auszeichnung_input.value.strip()

    conn = sqlite3.connect("army.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT message_id FROM personalakten WHERE user_id = ?", (user_id,)
    )
    row = cursor.fetchone()

    archive_channel = guild.get_channel(AKTEN_ARCHIV_KANAL_ID)
    msg_id = row[0] if row else None
    new_msg_id = msg_id

    embed = build_akte_embed(
        self.target_user,
        rang,
        nummer,
        eintraege,
        auszeichnung,
        interaction.user.display_name,
    )

    if archive_channel:
      if msg_id:
        try:
          msg = await archive_channel.fetch_message(msg_id)
          await msg.edit(embed=embed)
        except Exception:
          msg = await archive_channel.send(embed=embed)
          new_msg_id = msg.id
      else:
        msg = await archive_channel.send(embed=embed)
        new_msg_id = msg.id

    cursor.execute(
        """
        REPLACE INTO personalakten (user_id, username, dienstrang, dienstnummer, eintraege, auszeichnungen, message_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """,
        (
            user_id,
            username,
            rang,
            nummer,
            eintraege,
            auszeichnung,
            new_msg_id,
        ),
    )
    conn.commit()
    conn.close()

    log_action(
        "Personalakte aktualisiert",
        f"Akte von {username}",
        interaction.user.display_name,
    )

    await interaction.response.send_message(
        f"✅ Personalakte für **{username}** wurde aktualisiert und im"
        " Archiv gepostet!",
        ephemeral=True,
    )


class AktenSelectView(discord.ui.View):

  def __init__(self, action_type="edit"):
    super().__init__(timeout=60)
    self.action_type = action_type

  @discord.ui.select(
      cls=discord.ui.UserSelect, placeholder="Wähle das Mitglied aus..."
  )
  async def select_user(
      self, interaction: discord.Interaction, select: discord.ui.UserSelect
  ):
    target_user = select.values[0]
    if not isinstance(target_user, discord.Member):
      target_user = await interaction.guild.fetch_member(target_user.id)

    user_id = str(target_user.id)
    conn = sqlite3.connect("army.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT dienstrang, dienstnummer, eintraege, auszeichnungen FROM"
        " personalakten WHERE user_id = ?",
        (user_id,),
    )
    row = cursor.fetchone()
    conn.close()

    if self.action_type == "edit":
      rang = row[0] if row else ""
      nummer = row[1] if row else ""
      eintraege = row[2] if row else ""
      auszeichnung = row[3] if row else ""

      await interaction.response.send_modal(
          AkteBearbeitenModal(
              target_user, rang, nummer, eintraege, auszeichnung
          )
      )
    else:
      if not row:
        await interaction.response.send_message(
            f"❌ Für **{target_user.display_name}** existiert noch keine"
            " Personalakte.",
            ephemeral=True,
        )
      else:
        embed = build_akte_embed(
            target_user,
            row[0],
            row[1],
            row[2],
            row[3],
            "Datenbank-Abfrage",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class PersonalaktenPanelView(discord.ui.View):

  def __init__(self):
    super().__init__(timeout=None)

  @discord.ui.button(
      label="Akte erstellen / bearbeiten",
      style=discord.ButtonStyle.primary,
      emoji="📝",
      custom_id="btn_edit_akte",
  )
  async def edit_akte(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    await interaction.response.send_message(
        "Wähle das Mitglied aus, dessen Akte du erstellen oder bearbeiten"
        " möchtest:",
        view=AktenSelectView(action_type="edit"),
        ephemeral=True,
    )

  @discord.ui.button(
      label="Akte abrufen",
      style=discord.ButtonStyle.secondary,
      emoji="🔍",
      custom_id="btn_view_akte",
  )
  async def view_akte(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    await interaction.response.send_message(
        "Wähle das Mitglied aus, dessen Akte du einsehen möchtest:",
        view=AktenSelectView(action_type="view"),
        ephemeral=True,
    )


# --- AUFSTELLUNG / APPEL ---
class AufstellungData:

  def __init__(self, thema, datum_zeit, rolle_mention):
    self.thema = thema
    self.datum_zeit = datum_zeit
    self.rolle = rolle_mention
    self.komme = set()
    self.komme_nicht = set()
    self.komme_spaeter = set()

  def build_embed(self):
    conn = sqlite3.connect("army.db")
    cursor = conn.cursor()
    cursor.execute("SELECT username, zeitraum FROM abmeldungen")
    abgemeldete_rows = cursor.fetchall()
    conn.close()

    embed = discord.Embed(
        title="🪖 Dienstantritt & Appell",
        description=f"**{self.datum_zeit}**\n\n**Grund / Thema:** {self.thema}\n**Einheit:** {self.rolle}",
        color=discord.Color.from_rgb(46, 204, 113),
    )

    komme_str = (
        "\n".join([f"• {u}" for u in self.komme]) if self.komme else "Keine"
    )
    nicht_str = (
        "\n".join([f"• {u}" for u in self.komme_nicht])
        if self.komme_nicht
        else "Keine"
    )
    spaeter_str = (
        "\n".join([f"• {u}" for u in self.komme_spaeter])
        if self.komme_spaeter
        else "Keine"
    )

    if not abgemeldete_rows:
      abgemeldet_str = "Keine"
    else:
      abgemeldet_str = ""
      for row in abgemeldete_rows:
        abgemeldet_str += f"• **{row[0]}** (Urlaub: {row[1]})\n"

    embed.add_field(
        name=f"Dienstbereit ({len(self.komme)})", value=komme_str, inline=False
    )
    embed.add_field(
        name=f"Nicht im Dienst ({len(self.komme_nicht)})",
        value=nicht_str,
        inline=False,
    )
    embed.add_field(
        name=f"Verspätet ({len(self.komme_spaeter)})",
        value=spaeter_str,
        inline=False,
    )
    embed.add_field(
        name="Offiziell abgemeldet (Urlaub)",
        value=abgemeldet_str,
        inline=False,
    )

    embed.set_footer(
        text=f"HQ Meldung vom {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    )
    return embed


class AufstellungView(discord.ui.View):

  def __init__(self, data: AufstellungData):
    super().__init__(timeout=None)
    self.data = data

  async def update_attendance(
      self, interaction: discord.Interaction, status: str
  ):
    user_mention = interaction.user.mention
    self.data.komme.discard(user_mention)
    self.data.komme_nicht.discard(user_mention)
    self.data.komme_spaeter.discard(user_mention)

    if status == "komme":
      self.data.komme.add(user_mention)
      msg = "Du hast dich als **Dienstbereit** gemeldet! ✅"
    elif status == "nicht":
      self.data.komme_nicht.add(user_mention)
      msg = "Du hast dich als **Nicht im Dienst** gemeldet! ❌"
    elif status == "spaeter":
      self.data.komme_spaeter.add(user_mention)
      msg = "Du hast dich als **Verspätet** gemeldet! ⏳"

    await interaction.message.edit(embed=self.data.build_embed())
    await interaction.response.send_message(msg, ephemeral=True)

  @discord.ui.button(
      label="Dienstbereit",
      style=discord.ButtonStyle.green,
      custom_id="btn_komme",
  )
  async def komme(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    await self.update_attendance(interaction, "komme")

  @discord.ui.button(
      label="Nicht im Dienst",
      style=discord.ButtonStyle.red,
      custom_id="btn_nicht",
  )
  async def nicht(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    await self.update_attendance(interaction, "nicht")

  @discord.ui.button(
      label="Verspätet",
      style=discord.ButtonStyle.secondary,
      custom_id="btn_spaeter",
  )
  async def spaeter(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    await self.update_attendance(interaction, "spaeter")


# --- TICKET SYSTEM (NUR FÜR TICKETBEARBEITER & ADMINS) ---
class TicketErstellenModal(
    discord.ui.Modal, title="Support-Ticket / Beschwerde"
):
  thema = discord.ui.TextInput(
      label="Betreff / Thema", placeholder="z.B. Beschwerde über Soldat X"
  )
  beschreibung = discord.ui.TextInput(
      label="Genaue Beschreibung",
      style=discord.TextStyle.paragraph,
      placeholder="Beschreibe dein Anliegen so genau wie möglich...",
  )

  async def on_submit(self, interaction: discord.Interaction):
    guild = interaction.guild
    user = interaction.user

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        user: discord.PermissionOverwrite(
            read_messages=True, send_messages=True
        ),
        guild.me: discord.PermissionOverwrite(
            read_messages=True, send_messages=True
        ),
    }

    tb_role = discord.utils.get(guild.roles, name=ROLLE_TICKETBEARBEITER)
    for role in guild.roles:
      if role.permissions.administrator or (tb_role and role == tb_role):
        overwrites[role] = discord.PermissionOverwrite(
            read_messages=True, send_messages=True
        )

    channel_name = f"ticket-{user.name}".lower().replace(" ", "-")
    ticket_channel = await guild.create_text_channel(
        name=channel_name, overwrites=overwrites
    )

    embed = discord.Embed(
        title=f"📩 Support Ticket: {self.thema.value}",
        description=(
            f"**Erstellt von:** {user.mention}\n**Thema:**"
            f" {self.thema.value}\n\n**Beschreibung:**\n{self.beschreibung.value}"
        ),
        color=discord.Color.from_rgb(231, 76, 60),
    )

    class TicketCloseView(discord.ui.View):

      def __init__(self):
        super().__init__(timeout=None)

      @discord.ui.button(
          label="Ticket schließen",
          style=discord.ButtonStyle.red,
          emoji="🔒",
          custom_id="btn_close_ticket",
      )
      async def close(
          self, interaction_close: discord.Interaction, button: discord.ui.Button
      ):
        await interaction_close.response.send_message(
            "🔒 Ticket wird in 5 Sekunden geschlossen..."
        )
        log_action(
            "Ticket Geschlossen",
            f"Kanal: {interaction_close.channel.name}",
            interaction_close.user.display_name,
        )
        import asyncio

        await asyncio.sleep(5)
        await interaction_close.channel.delete()

    await ticket_channel.send(
        content=f"{user.mention} Willkommen in deinem Ticket!",
        embed=embed,
        view=TicketCloseView(),
    )

    log_channel = guild.get_channel(TICKET_LOG_KANAL_ID)
    if log_channel:
      log_embed = discord.Embed(
          title="🚨 Neues Support-Ticket erstellt",
          description=(
              f"**User:** {user.mention}\n**Kanal:**"
              f" {ticket_channel.mention}\n**Betreff:** {self.thema.value}"
          ),
          color=discord.Color.orange(),
      )
      await log_channel.send(embed=log_embed)

    log_action("Ticket Erstellt", f"Thema: {self.thema.value}", user.display_name)

    await interaction.response.send_message(
        f"✅ Dein Ticket wurde erstellt: {ticket_channel.mention}",
        ephemeral=True,
    )


# --- BEWERBUNGSSYSTEM ---
class BewerbungModal(discord.ui.Modal, title="Bewerbung bei der Army"):
  ic_name = discord.ui.TextInput(
      label="Dein IC-Name", placeholder="z.B. John Doe", required=True
  )
  ooc_alter = discord.ui.TextInput(
      label="Dein OOC-Alter", placeholder="z.B. 18", required=True
  )
  bewerbungstext = discord.ui.TextInput(
      label="Warum möchtest du zur Army?",
      style=discord.TextStyle.paragraph,
      placeholder=(
          "Schreibe hier deinen Bewerbungstext und deine bisherigen"
          " Erfahrungen..."
      ),
      required=True,
  )

  async def on_submit(self, interaction: discord.Interaction):
    guild = interaction.guild
    user = interaction.user

    log_channel = guild.get_channel(BEWERBUNG_LOG_KANAL_ID)
    if not log_channel:
      await interaction.response.send_message(
          "❌ Fehler: Der Admin-Bewerbungskanal (`BEWERBUNG_LOG_KANAL_ID`) wurde"
          " nicht gefunden!",
          ephemeral=True,
      )
      return

    embed = discord.Embed(
        title=f"🎖️ Neue Bewerbung: {self.ic_name.value}",
        description=(
            f"**Bewerber:** {user.mention}\n"
            f"**IC-Name:** {self.ic_name.value}\n"
            f"**OOC-Alter:** {self.ooc_alter.value}\n\n"
            f"**Bewerbungstext:**\n{self.bewerbungstext.value}"
        ),
        color=discord.Color.blue(),
    )
    embed.set_thumbnail(
        url=user.avatar.url if user.avatar else user.default_avatar.url
    )

    ic_val = self.ic_name.value

    class AdminBewerbungActionView(discord.ui.View):

      def __init__(self):
        super().__init__(timeout=None)

      @discord.ui.button(
          label="Annehmen & Gesprächskanal öffnen",
          style=discord.ButtonStyle.green,
          emoji="✅",
          custom_id="btn_accept_and_open_chat",
      )
      async def accept(
          self,
          interaction_act: discord.Interaction,
          button: discord.ui.Button,
      ):
        ausbilder_role = discord.utils.get(guild.roles, name=ROLLE_AUSBILDER)
        is_admin = interaction_act.user.guild_permissions.administrator
        has_ausbilder = (
            ausbilder_role in interaction_act.user.roles
            if ausbilder_role
            else False
        )

        if not (is_admin or has_ausbilder):
          await interaction_act.response.send_message(
              "❌ Dazu hast du keine Berechtigung!", ephemeral=True
          )
          return

        await interaction_act.response.defer(ephemeral=True)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                read_messages=False
            ),
            user: discord.PermissionOverwrite(
                read_messages=True, send_messages=True
            ),
            guild.me: discord.PermissionOverwrite(
                read_messages=True, send_messages=True
            ),
        }

        for role in guild.roles:
          if role.permissions.administrator or (
              ausbilder_role and role == ausbilder_role
          ):
            overwrites[role] = discord.PermissionOverwrite(
                read_messages=True, send_messages=True
            )

        clean_name = ic_val.lower().replace(" ", "-")
        channel_name = f"bewerbung-{clean_name}"
        app_channel = await guild.create_text_channel(
            name=channel_name, overwrites=overwrites
        )

        ping_text = (
            f"{user.mention} {ausbilder_role.mention if ausbilder_role else ''}"
        )
        await app_channel.send(
            content=(
                f"{ping_text} **Bewerbung angenommen!** Klärt hier im Chat ab,"
                " wann und wo das Treffen / Gespräch stattfindet."
            ),
            embed=embed,
        )

        try:
          await user.send(
              "🎉 Deine Bewerbung wurde von der Führungsebene vorab"
              " **gesichtet und angenommen**! Es wurde ein privater"
              f" Gesprächskanal ({app_channel.mention}) auf dem Server"
              " erstellt, um das Treffen zu vereinbaren."
          )
        except Exception:
          pass

        log_action(
            "Bewerbung Angenommen (Chat)",
            f"Bewerber: {user.display_name} -> Kanal: {app_channel.name}",
            interaction_act.user.display_name,
        )

        await interaction_act.message.edit(
            content=(
                f"✅ **Bewerbung angenommen von {interaction_act.user.mention}**"
                f" — Gesprächskanal erstellt: {app_channel.mention}"
            ),
            view=None,
        )
        await interaction_act.followup.send(
            f"✅ Gesprächskanal erstellt: {app_channel.mention}", ephemeral=True
        )

      @discord.ui.button(
          label="Ablehnen",
          style=discord.ButtonStyle.red,
          emoji="❌",
          custom_id="btn_reject_app",
      )
      async def reject(
          self,
          interaction_act: discord.Interaction,
          button: discord.ui.Button,
      ):
        ausbilder_role = discord.utils.get(guild.roles, name=ROLLE_AUSBILDER)
        is_admin = interaction_act.user.guild_permissions.administrator
        has_ausbilder = (
            ausbilder_role in interaction_act.user.roles
            if ausbilder_role
            else False
        )

        if not (is_admin or has_ausbilder):
          await interaction_act.response.send_message(
              "❌ Dazu hast du keine Berechtigung!", ephemeral=True
          )
          return

        await interaction_act.response.defer(ephemeral=True)

        try:
          await user.send(
              "Leider wurde deine Bewerbung bei der Army nach genauer Prüfung"
              " **abgelehnt**."
          )
        except Exception:
          pass

        log_action(
            "Bewerbung Abgelehnt",
            f"Bewerber: {user.display_name}",
            interaction_act.user.display_name,
        )

        await interaction_act.message.edit(
            content=(
                f"❌ **Bewerbung abgelehnt von {interaction_act.user.mention}**"
            ),
            view=None,
        )
        await interaction_act.followup.send(
            "Bewerbung erfolgreich abgelehnt.", ephemeral=True
        )

    await log_channel.send(embed=embed, view=AdminBewerbungActionView())
    log_action("Bewerbung Eingereicht", f"IC: {self.ic_name.value}", user.name)

    await interaction.response.send_message(
        "✅ Deine Bewerbung wurde erfolgreich eingereicht und wird von der"
        " Führungsebene im HQ geprüft!",
        ephemeral=True,
    )


class VerifizierungModal(discord.ui.Modal, title="Besucher Verifizierung"):
  ic_name = discord.ui.TextInput(
      label="Dein IC-Name (Vorname Nachname)",
      placeholder="z.B. John Doe",
      required=True,
  )

  async def on_submit(self, interaction: discord.Interaction):
    name = self.ic_name.value.strip()
    guild = interaction.guild
    role = discord.utils.get(guild.roles, name=ROLLE_BESUCHER)

    if not role:
      await interaction.response.send_message(
          f"❌ Die Rolle **{ROLLE_BESUCHER}** existiert nicht auf diesem Server!",
          ephemeral=True,
      )
      return

    try:
      await interaction.user.add_roles(role)
      try:
        await interaction.user.edit(nick=name)
      except Exception:
        pass

      log_action("Verifizierung", f"Besucher: {name}", interaction.user.name)

      await interaction.response.send_message(
          f"✅ Erfolgreich verifiziert als **Besucher ({name})**!",
          ephemeral=True,
      )
    except Exception:
      await interaction.response.send_message(
          "❌ Fehler beim Zuweisen der Besucher-Rolle.", ephemeral=True
      )


class AbmeldungEinreichenModal(discord.ui.Modal, title="Dienstbefreiung / Urlaub"):
  zeitraum = discord.ui.TextInput(
      label="Zeitraum (TT.MM.JJJJ - TT.MM.JJJJ)",
      placeholder="z.B. 20.10.2026 - 25.10.2026",
  )
  grund = discord.ui.TextInput(
      label="Grund der Abwesenheit", placeholder="z.B. Lehrgang / Heimaturlaub"
  )

  async def on_submit(self, interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    username = interaction.user.display_name

    conn = sqlite3.connect("army.db")
    cursor = conn.cursor()
    cursor.execute(
        "REPLACE INTO abmeldungen (user_id, username, zeitraum, grund) VALUES"
        " (?, ?, ?, ?)",
        (user_id, username, self.zeitraum.value, self.grund.value),
    )
    conn.commit()
    conn.close()

    log_action(
        "Urlaub/Abmeldung",
        f"Zeitraum: {self.zeitraum.value} | Grund: {self.grund.value}",
        username,
    )

    global live_abmeldung_message
    if live_abmeldung_message:
      try:
        await live_abmeldung_message.edit(embed=get_abmeldungen_embed())
      except Exception:
        pass

    await interaction.response.send_message(
        "✅ Dienstbefreiung wurde eingereicht!", ephemeral=True
    )


class EinlagernModal(
    discord.ui.Modal, title="Ausrüstung / Waffen einlagern"
):
  item_name = discord.ui.TextInput(
      label="Gegenstand / Waffe", placeholder="z.B. Carbine Rifle / M4"
  )
  amount = discord.ui.TextInput(label="Anzahl", placeholder="z.B. 10")

  async def on_submit(self, interaction: discord.Interaction):
    try:
      qty = int(self.amount.value)
    except ValueError:
      await interaction.response.send_message(
          "❌ Die Menge muss eine Zahl sein!", ephemeral=True
      )
      return

    name = self.item_name.value.strip()
    conn = sqlite3.connect("army.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT quantity, min_quantity FROM items WHERE name = ?", (name,)
    )
    row = cursor.fetchone()

    if row:
      new_qty = row[0] + qty
      cursor.execute(
          "UPDATE items SET quantity = ? WHERE name = ?", (new_qty, name)
      )
    else:
      cursor.execute(
          "INSERT INTO items (name, quantity, min_quantity) VALUES (?, ?, ?)",
          (name, qty, 5),
      )
    conn.commit()
    conn.close()

    log_action(
        "Waffenkammer +", f"{qty}x {name}", interaction.user.display_name
    )

    global live_bestand_message
    if live_bestand_message:
      try:
        await live_bestand_message.edit(embed=get_bestand_embed())
      except Exception:
        pass

    await interaction.response.send_message(
        f"✅ Erfolgreich **{qty}x {name}** in der Waffenkammer eingelagert!",
        ephemeral=True,
    )


class AuslagernModal(discord.ui.Modal, title="Ausrüstung entnehmen"):
  item_name = discord.ui.TextInput(
      label="Gegenstand / Waffe", placeholder="z.B. Weste / Schutzweste"
  )
  amount = discord.ui.TextInput(label="Anzahl", placeholder="z.B. 2")
  reason = discord.ui.TextInput(
      label="Einsatzzweck", placeholder="z.B. Streife / Großeinsatz"
  )

  async def on_submit(self, interaction: discord.Interaction):
    try:
      qty = int(self.amount.value)
    except ValueError:
      await interaction.response.send_message(
          "❌ Die Menge muss eine Zahl sein!", ephemeral=True
      )
      return

    name = self.item_name.value.strip()
    conn = sqlite3.connect("army.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT quantity, min_quantity FROM items WHERE name = ?", (name,)
    )
    row = cursor.fetchone()

    if not row or row[0] < qty:
      conn.close()
      await interaction.response.send_message(
          "❌ Nicht genügend Bestand in der Waffenkammer!", ephemeral=True
      )
      return

    new_qty = row[0] - qty
    cursor.execute(
        "UPDATE items SET quantity = ? WHERE name = ?", (new_qty, name)
    )
    conn.commit()
    conn.close()

    log_action(
        "Waffenkammer -",
        f"{qty}x {name} (Zweck: {self.reason.value})",
        interaction.user.display_name,
    )

    global live_bestand_message
    if live_bestand_message:
      try:
        await live_bestand_message.edit(embed=get_bestand_embed())
      except Exception:
        pass

    await interaction.response.send_message(
        f"✅ Erfolgreich **{qty}x {name}** aus der Waffenkammer entnommen!",
        ephemeral=True,
    )


class SoldEintragenModal(
    discord.ui.Modal, title="Kassenbeitrag / Sold eintragen"
):
  member_name = discord.ui.TextInput(
      label="Soldat (Name)", placeholder="z.B. PVT John Doe"
  )
  status_wahl = discord.ui.TextInput(
      label="Status (bezahlt / offen / befreit)", placeholder="z.B. bezahlt"
  )

  async def on_submit(self, interaction: discord.Interaction):
    name = self.member_name.value.strip()
    status_input = self.status_wahl.value.strip().lower()

    if status_input not in ["bezahlt", "offen", "befreit"]:
      await interaction.response.send_message(
          "❌ Ungültiger Status! Nutze: `bezahlt`, `offen` oder `befreit`.",
          ephemeral=True,
      )
      return

    conn = sqlite3.connect("army.db")
    cursor = conn.cursor()
    cursor.execute(
        "REPLACE INTO sold_kasse (user_id, username, status) VALUES (?, ?, ?)",
        (name, name, status_input),
    )
    conn.commit()
    conn.close()

    log_action(
        "Sold-Kasse",
        f"Soldat: {name} -> {status_input}",
        interaction.user.display_name,
    )

    global live_sold_message
    if live_sold_message:
      try:
        await live_sold_message.edit(embed=get_sold_embed())
      except Exception:
        pass

    await interaction.response.send_message(
        f"✅ Status für Soldat **{name}** auf **{status_input}** gesetzt!",
        ephemeral=True,
    )


class SoldWiderrufenModal(discord.ui.Modal, title="Eintrag widerrufen"):
  member_name = discord.ui.TextInput(
      label="Soldat (Name)", placeholder="z.B. PVT John Doe"
  )

  async def on_submit(self, interaction: discord.Interaction):
    name = self.member_name.value.strip()

    conn = sqlite3.connect("army.db")
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM sold_kasse WHERE username = ? OR user_id = ?", (name, name)
    )
    deleted = cursor.rowcount
    conn.commit()
    conn.close()

    if deleted > 0:
      log_action(
          "Sold-Kasse Widerrufen",
          f"Eintrag für {name} gelöscht",
          interaction.user.display_name,
      )
      global live_sold_message
      if live_sold_message:
        try:
          await live_sold_message.edit(embed=get_sold_embed())
        except Exception:
          pass

      await interaction.response.send_message(
          f"✅ Der Eintrag für **{name}** wurde gelöscht!", ephemeral=True
      )
    else:
      await interaction.response.send_message(
          f"❌ Kein Eintrag für **{name}** gefunden.", ephemeral=True
      )


# --- PERSISTENTE VIEWS ---
class TicketView(discord.ui.View):

  def __init__(self):
    super().__init__(timeout=None)

  @discord.ui.button(
      label="Ticket erstellen",
      style=discord.ButtonStyle.primary,
      emoji="📩",
      custom_id="btn_create_ticket",
  )
  async def ticket(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    await interaction.response.send_modal(TicketErstellenModal())


class BewerbungView(discord.ui.View):

  def __init__(self):
    super().__init__(timeout=None)

  @discord.ui.button(
      label="Bewerbung einreichen",
      style=discord.ButtonStyle.green,
      emoji="📝",
      custom_id="btn_submit_bewerbung",
  )
  async def bewerbung(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    await interaction.response.send_modal(BewerbungModal())


class ArmoryView(discord.ui.View):

  def __init__(self):
    super().__init__(timeout=None)

  @discord.ui.button(
      label="Einlagern",
      style=discord.ButtonStyle.green,
      emoji="📥",
      custom_id="btn_armory_einlagern",
  )
  async def einlagern(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    await interaction.response.send_modal(EinlagernModal())

  @discord.ui.button(
      label="Entnehmen",
      style=discord.ButtonStyle.red,
      emoji="📤",
      custom_id="btn_armory_auslagern",
  )
  async def auslagern(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    await interaction.response.send_modal(AuslagernModal())

  @discord.ui.button(
      label="Waffenbestand",
      style=discord.ButtonStyle.secondary,
      emoji="🪖",
      custom_id="btn_armory_bestand",
  )
  async def bestand(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    await interaction.response.send_message(
        embed=get_bestand_embed(), ephemeral=True
    )


class VerifizierungView(discord.ui.View):

  def __init__(self):
    super().__init__(timeout=None)

  @discord.ui.button(
      label="Als Besucher Verifizieren",
      style=discord.ButtonStyle.green,
      emoji="✅",
      custom_id="btn_rekrutierung",
  )
  async def verifizieren(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    await interaction.response.send_modal(VerifizierungModal())


class AbmeldungView(discord.ui.View):

  def __init__(self):
    super().__init__(timeout=None)

  @discord.ui.button(
      label="Urlaub / Abmeldung einreichen",
      style=discord.ButtonStyle.green,
      custom_id="btn_abmeldung_einreichen",
  )
  async def einreichen(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    await interaction.response.send_modal(AbmeldungEinreichenModal())

  @discord.ui.button(
      label="Dienst wiederantreten",
      style=discord.ButtonStyle.red,
      custom_id="btn_abmeldung_loeschen",
  )
  async def loeschen(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    user_id = str(interaction.user.id)
    username = interaction.user.display_name
    conn = sqlite3.connect("army.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM abmeldungen WHERE user_id = ?", (user_id,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()

    if deleted > 0:
      log_action("Dienstantritt", "Vorzeitig aus Urlaub zurück", username)

    global live_abmeldung_message
    if live_abmeldung_message:
      try:
        await live_abmeldung_message.edit(embed=get_abmeldungen_embed())
      except Exception:
        pass

    if deleted > 0:
      await interaction.response.send_message(
          "✅ Du hast dich wieder zurück zum Dienst gemeldet!", ephemeral=True
      )
    else:
      await interaction.response.send_message(
          "❌ Du hast aktuell keine aktive Abmeldung.", ephemeral=True
      )


class SoldKasseView(discord.ui.View):

  def __init__(self):
    super().__init__(timeout=None)

  @discord.ui.button(
      label="Beitrag eintragen",
      style=discord.ButtonStyle.green,
      custom_id="btn_sold_eintragen",
  )
  async def abgabe_eintragen(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    await interaction.response.send_modal(SoldEintragenModal())

  @discord.ui.button(
      label="Rückgängig",
      style=discord.ButtonStyle.red,
      custom_id="btn_sold_rueckgaengig",
  )
  async def rueckgaengig(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    await interaction.response.send_modal(SoldWiderrufenModal())

  @discord.ui.button(
      label="Mein Status",
      style=discord.ButtonStyle.blurple,
      custom_id="btn_sold_status",
  )
  async def mein_status(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    user_id = str(interaction.user.id)
    username = interaction.user.display_name
    conn = sqlite3.connect("army.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT status FROM sold_kasse WHERE user_id = ? OR username = ?",
        (user_id, username),
    )
    row = cursor.fetchone()
    conn.close()

    if row:
      status_map = {
          "bezahlt": "Bezahlt ✅",
          "offen": "Ausstehend ❌",
          "befreit": "Befreit 🟦",
      }
      status_text = status_map.get(row[0], row[0])
      await interaction.response.send_message(
          f"ℹ️ Dein Status beim Kassenbeitrag: **{status_text}**",
          ephemeral=True,
      )
    else:
      await interaction.response.send_message(
          "ℹ️ Dein Status: **Ausstehend ❌**", ephemeral=True
      )


# --- BOT SETUP ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


@tasks.loop(time=UHRZEIT_POST)
async def tages_aufstellung_task():
  heute_wochentag = datetime.now().weekday()
  if heute_wochentag not in [2, 6]:
    return

  channel = bot.get_channel(DIENST_KANAL_ID)
  if not channel:
    return

  guild = channel.guild
  role = discord.utils.get(guild.roles, name=ROLLE_ARMY)
  role_mention = role.mention if role else "@everyone"

  heute_datum = datetime.now().strftime("%d.%m.%Y")
  datum_text = f"Heute ({heute_datum}) um 20:00 Uhr"

  data = AufstellungData(
      thema="Großer Dienstappell",
      datum_zeit=datum_text,
      rolle_mention=role_mention,
  )
  view = AufstellungView(data)

  await channel.send(
      content=f"{role_mention} 📢 **Achtung! Antreten zum Dienstappell! Bitte alle abstimmen!**",
      embed=data.build_embed(),
      view=view,
  )


@bot.event
async def on_ready():
  bot.add_view(ArmoryView())
  bot.add_view(VerifizierungView())
  bot.add_view(AbmeldungView())
  bot.add_view(SoldKasseView())
  bot.add_view(TicketView())
  bot.add_view(BewerbungView())
  bot.add_view(PersonalaktenPanelView())
  bot.add_view(ZimmerPanelView())

  try:
    synced = await bot.tree.sync()
    print(f"Synced {len(synced)} slash commands.")
  except Exception as e:
    print(e)

  if not tages_aufstellung_task.is_running():
    tages_aufstellung_task.start()

  print(f"Eingeloggt als {bot.user}")


# --- COMMANDS ---
@bot.tree.command(
    name="zimmerpanel", description="Sendet das Zimmeraufteilungs-Verwaltungspanel"
)
@app_commands.checks.has_permissions(administrator=True)
async def zimmerpanel(interaction: discord.Interaction):
  embed = discord.Embed(
      title="🏠 Zimmeraufteilung - Verwaltung",
      description=(
          "Verwalte die Bettenbelegung aller 4 Häuser:\n\n"
          "• **🛏️ Bett zuweisen:** Weise einem Mitglied ein Bett in einem Haus"
          " zu.\n"
          "• **🚪 Bett räumen:** Entferne ein Mitglied aus seinem Zimmer.\n"
          "• **📋 Übersicht anzeigen:** Rufe die Belegung vertraulich im Chat"
          " ab."
      ),
      color=discord.Color.from_rgb(52, 152, 219),
  )
  await interaction.channel.send(embed=embed, view=ZimmerPanelView())
  await interaction.response.send_message(
      "Zimmeraufteilungs-Panel gesendet!", ephemeral=True
  )


@bot.tree.command(
    name="zimmerplan", description="Erstellt die Live-Zimmeraufteilung"
)
@app_commands.checks.has_permissions(administrator=True)
async def zimmerplan(interaction: discord.Interaction):
  global live_zimmer_messages
  live_zimmer_messages = []

  embeds = get_zimmer_embeds()
  await interaction.response.send_message(
      "Live-Zimmerplan wird eingerichtet...", ephemeral=True
  )

  for embed in embeds:
    msg = await interaction.channel.send(embed=embed)
    live_zimmer_messages.append(msg)


@bot.tree.command(
    name="aktenpanel", description="Sendet das Personalakten-Verwaltungspanel"
)
@app_commands.checks.has_permissions(administrator=True)
async def aktenpanel(interaction: discord.Interaction):
  embed = discord.Embed(
      title="📂 Personalakten - Hauptquartier",
      description=(
          "Verwalte und erstelle Personalakten der Einheitsmitglieder.\n\n"
          "• **Akte erstellen / bearbeiten:** Wähle ein Mitglied aus und füge"
          " Notizen, Rang oder Lehrgänge hinzu.\n"
          "• **Akte abrufen:** Zeigt dir die Akte eines Mitglieds vertraulich"
          " an."
      ),
      color=discord.Color.from_rgb(44, 62, 80),
  )
  await interaction.channel.send(embed=embed, view=PersonalaktenPanelView())
  await interaction.response.send_message(
      "Personalakten-Panel gesendet!", ephemeral=True
  )


@bot.tree.command(
    name="ticketpanel", description="Sendet das Support & Ticket Panel"
)
@app_commands.checks.has_permissions(administrator=True)
async def ticketpanel(interaction: discord.Interaction):
  embed = discord.Embed(
      title="📩 Support & Beschwerden",
      description=(
          "Hast du Fragen, Probleme oder eine Beschwerde?\nKlicke auf den Button"
          " unten, um ein privates Ticket mit der Führungsebene zu"
          " eröffnen."
      ),
      color=discord.Color.from_rgb(231, 76, 60),
  )
  await interaction.channel.send(embed=embed, view=TicketView())
  await interaction.response.send_message(
      "Ticket-Panel gesendet!", ephemeral=True
  )


@bot.tree.command(
    name="bewerbungspanel", description="Sendet das Bewerbungspanel"
)
@app_commands.checks.has_permissions(administrator=True)
async def bewerbungspanel(interaction: discord.Interaction):
  embed = discord.Embed(
      title="📝 Bewerbung bei der Army",
      description=(
          "Möchtest du der Army beitreten?\nKlicke auf den Button unten, um"
          " deine Bewerbung einzureichen."
      ),
      color=discord.Color.from_rgb(46, 204, 113),
  )
  await interaction.channel.send(embed=embed, view=BewerbungView())
  await interaction.response.send_message(
      "Bewerbungs-Panel gesendet!", ephemeral=True
  )


@bot.tree.command(
    name="verifizierung", description="Sendet das Besucher-Verifizierungspanel"
)
@app_commands.checks.has_permissions(administrator=True)
async def verifizierung(interaction: discord.Interaction):
  embed = discord.Embed(
      title="🎖️ Server Verifizierung (Besucher)",
      description=(
          "Willkommen! Klicke unten, um dich zu verifizieren. Du erhältst"
          f" automatisch die **{ROLLE_BESUCHER}**-Rolle."
      ),
      color=discord.Color.from_rgb(52, 152, 219),
  )
  await interaction.channel.send(embed=embed, view=VerifizierungView())
  await interaction.response.send_message(
      "Verifizierungs-Panel gesendet!", ephemeral=True
  )


@bot.tree.command(
    name="waffenkammer", description="Sendet das Waffenkammer-Panel"
)
@app_commands.checks.has_permissions(administrator=True)
async def waffenkammer(interaction: discord.Interaction):
  embed = discord.Embed(
      title="🪖 Waffenkammer Verwaltung",
      description=(
          "Verwalte Waffen und Munition über die Buttons. Jede Entnahme wird"
          " genau protokolliert."
      ),
      color=discord.Color.from_rgb(46, 125, 50),
  )
  await interaction.channel.send(embed=embed, view=ArmoryView())
  await interaction.response.send_message(
      "Waffenkammer-Panel erstellt!", ephemeral=True
  )


@bot.tree.command(
    name="bestandskanal", description="Erstellt die Live-Waffenkammeranzeige"
)
@app_commands.checks.has_permissions(administrator=True)
async def bestandskanal(interaction: discord.Interaction):
  global live_bestand_message
  live_bestand_message = await interaction.channel.send(
      embed=get_bestand_embed()
  )
  await interaction.response.send_message(
      "Live-Bestand erfolgreich eingerichtet!", ephemeral=True
  )


@bot.tree.command(
    name="dienstabmeldungen", description="Sendet das Abmeldungs-Panel"
)
@app_commands.checks.has_permissions(administrator=True)
async def dienstabmeldungen(interaction: discord.Interaction):
  embed = discord.Embed(
      title="📋 Dienstbefreiungen & Urlaub",
      description="Reiche hier deine Abmeldung vom Dienst ein.",
      color=discord.Color.from_rgb(41, 128, 185),
  )
  await interaction.channel.send(embed=embed, view=AbmeldungView())
  await interaction.response.send_message(
      "Abmeldungs-Panel gesendet!", ephemeral=True
  )


@bot.tree.command(
    name="urlaubsliste", description="Erstellt eine Live-Urlaubsliste"
)
@app_commands.checks.has_permissions(administrator=True)
async def urlaubsliste(interaction: discord.Interaction):
  global live_abmeldung_message
  live_abmeldung_message = await interaction.channel.send(
      embed=get_abmeldungen_embed()
  )
  await interaction.response.send_message(
      "Live-Urlaubsliste eingerichtet!", ephemeral=True
  )


@bot.tree.command(
    name="soldkasse", description="Sendet das Kassenbeitrags-Panel"
)
@app_commands.checks.has_permissions(administrator=True)
async def soldkasse(interaction: discord.Interaction):
  global live_sold_message
  live_sold_message = await interaction.channel.send(
      embed=get_sold_embed(), view=SoldKasseView()
  )
  await interaction.response.send_message(
      "Soldkassen-Panel erstellt!", ephemeral=True
  )


@bot.tree.command(name="logpanel", description="Erstellt das Live-HQ-Log")
@app_commands.checks.has_permissions(administrator=True)
async def logpanel(interaction: discord.Interaction):
  await interaction.response.defer(ephemeral=True)
  global live_log_message
  live_log_message = await interaction.channel.send(embed=get_log_embed())
  await interaction.followup.send(
      "Live-Log-Panel erstellt!", ephemeral=True
  )


@bot.tree.command(
    name="dienstplan", description="Postet den Dienstplan / Kalender"
)
@app_commands.checks.has_permissions(administrator=True)
async def dienstplan(interaction: discord.Interaction):
  global live_kalender_message
  live_kalender_message = await interaction.channel.send(
      embed=get_kalender_embed()
  )
  await interaction.response.send_message("Dienstplan gepostet!", ephemeral=True)


@bot.tree.command(
    name="einsatz_erstellen", description="Fügt eine Übung/Manöver hinzu"
)
@app_commands.checks.has_permissions(administrator=True)
async def einsatz_erstellen(
    interaction: discord.Interaction,
    titel: str,
    datum_uhrzeit: str,
    beschreibung: str,
):
  conn = sqlite3.connect("army.db")
  cursor = conn.cursor()
  cursor.execute(
      "INSERT INTO termine (titel, datum_uhrzeit, beschreibung, erstellt_von)"
      " VALUES (?, ?, ?, ?)",
      (titel, datum_uhrzeit, beschreibung, interaction.user.display_name),
  )
  conn.commit()
  conn.close()

  log_action(
      "Einsatz terminiert",
      f"{titel} am {datum_uhrzeit}",
      interaction.user.display_name,
  )

  global live_kalender_message
  if live_kalender_message:
    try:
      await live_kalender_message.edit(embed=get_kalender_embed())
    except Exception:
      pass

  await interaction.response.send_message(
      f"✅ Einsatz **{titel}** am **{datum_uhrzeit}** eingetragen!",
      ephemeral=True,
  )


@bot.tree.command(
    name="einsatz_loeschen", description="Löscht einen Einsatz anhand der ID"
)
@app_commands.checks.has_permissions(administrator=True)
async def einsatz_loeschen(interaction: discord.Interaction, einsatz_id: int):
  conn = sqlite3.connect("army.db")
  cursor = conn.cursor()
  cursor.execute("DELETE FROM termine WHERE id = ?", (einsatz_id,))
  deleted = cursor.rowcount
  conn.commit()
  conn.close()

  if deleted > 0:
    log_action(
        "Einsatz storniert",
        f"ID #{einsatz_id}",
        interaction.user.display_name,
    )
    global live_kalender_message
    if live_kalender_message:
      try:
        await live_kalender_message.edit(embed=get_kalender_embed())
      except Exception:
        pass
    await interaction.response.send_message(
        f"✅ Einsatz ID #{einsatz_id} wurde gelöscht!", ephemeral=True
    )
  else:
    await interaction.response.send_message(
        f"❌ Keinen Einsatz mit der ID #{einsatz_id} gefunden.", ephemeral=True
    )


@bot.tree.command(
    name="setzbeitrag", description="Ändert den Pflichtbeitrag der Kasse"
)
@app_commands.checks.has_permissions(administrator=True)
async def setzbeitrag(interaction: discord.Interaction, betrag: int):
  conn = sqlite3.connect("army.db")
  cursor = conn.cursor()
  cursor.execute(
      "REPLACE INTO einstellungen (key, value) VALUES ('sold_betrag', ?)",
      (str(betrag),),
  )
  conn.commit()
  conn.close()

  log_action(
      "Finanzanpassung",
      f"Beitrag auf {betrag:,}$ gesetzt",
      interaction.user.display_name,
  )

  global live_sold_message
  if live_sold_message:
    try:
      await live_sold_message.edit(embed=get_sold_embed())
    except Exception:
      pass

  await interaction.response.send_message(
      f"✅ Kassenbeitrag auf **{betrag:,}$** geändert!", ephemeral=True
  )


@bot.tree.command(
    name="dienstappell", description="Manuelle Dienstabfrage senden"
)
@app_commands.checks.has_permissions(administrator=True)
async def dienstappell(
    interaction: discord.Interaction,
    thema: str,
    datum_zeit: str,
    rolle: discord.Role,
):
  data = AufstellungData(
      thema=thema, datum_zeit=datum_zeit, rolle_mention=rolle.mention
  )
  view = AufstellungView(data)

  await interaction.channel.send(
      content=f"{rolle.mention} 📢 **Dienstappell: {thema}**",
      embed=data.build_embed(),
      view=view,
  )
  log_action(
      "Dienstappell",
      f"Thema: {thema} ({datum_zeit})",
      interaction.user.display_name,
  )
  await interaction.response.send_message(
      "Appell gesendet!", ephemeral=True
  )


# --- WEBSERVER FÜR RENDER (Keep-Alive) ---
class DummyHandler(BaseHTTPRequestHandler):

  def do_GET(self):
    self.send_response(200)
    self.send_header("Content-type", "text/plain")
    self.end_headers()
    self.wfile.write(b"Army Bot HQ is online!")

  def do_HEAD(self):
    self.send_response(200)
    self.send_header("Content-type", "text/plain")
    self.end_headers()

  def do_POST(self):
    self.send_response(200)
    self.end_headers()

  def log_message(self, format, *args):
    return


def run_server():
  port = int(os.getenv("PORT", 10000))
  server = HTTPServer(("0.0.0.0", port), DummyHandler)
  server.serve_forever()


threading.Thread(target=run_server, daemon=True).start()

# --- BOT STARTEN ---
TOKEN = os.getenv("DISCORD_TOKEN")
