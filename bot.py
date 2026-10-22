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
DIENST_KANAL_ID = 123456789012345678  # ERSETZE DIES DURCH DEINE KANAL-ID!
BEWERBUNG_LOG_KANAL_ID = 123456789012345678  # KANAL FÜR DIE ADMINGESICHTETEN BEWERBUNGEN!
TICKET_LOG_KANAL_ID = 123456789012345678  # KANAL FÜR TICKET-BENACHRICHTIGUNGEN!

ROLLE_ARMY = "Army"
ROLLE_BESUCHER = "Besucher"  # Rolle für verifizierte Besucher
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


# --- EMBED HELFER ---
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


# --- MODALS ---
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

    # Erstelle privaten Ticket-Kanal
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        user: discord.PermissionOverwrite(
            read_messages=True, send_messages=True
        ),
        guild.me: discord.PermissionOverwrite(
            read_messages=True, send_messages=True
        ),
    }

    # Admins auch Rechte geben
    for role in guild.roles:
      if role.permissions.administrator:
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

    # Benachrichtigung im Log-Kanal
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


class BewerbungModal(discord.ui.Modal, title="Bewerbung bei der Army"):
  ic_name = discord.ui.TextInput(
      label="Dein IC-Name", placeholder="z.B. John Doe"
  )
  ooc_alter = discord.ui.TextInput(
      label="Dein OOC-Alter", placeholder="z.B. 18"
  )
  bewerbungstext = discord.ui.TextInput(
      label="Warum möchtest du zur Army?",
      style=discord.TextStyle.paragraph,
      placeholder=(
          "Schreibe hier deinen Bewerbungstext und deine bisherigen"
          " Erfahrungen..."
      ),
  )

  async def on_submit(self, interaction: discord.Interaction):
    guild = interaction.guild
    user = interaction.user

    log_channel = guild.get_channel(BEWERBUNG_LOG_KANAL_ID)

    embed = discord.Embed(
        title=f"🎖️ Neue Bewerbung von {self.ic_name.value}",
        description=(
            f"**Discord-User:** {user.mention}\n**IC-Name:**"
            f" {self.ic_name.value}\n**OOC-Alter:**"
            f" {self.ooc_alter.value}\n\n**Bewerbungstext:**\n{self.bewerbungstext.value}"
        ),
        color=discord.Color.blue(),
    )
    embed.set_thumbnail(
        url=user.avatar.url if user.avatar else user.default_avatar.url
    )

    class BewerbungActionView(discord.ui.View):

      def __init__(self):
        super().__init__(timeout=None)

      @discord.ui.button(
          label="Annehmen",
          style=discord.ButtonStyle.green,
          custom_id="btn_accept_app",
      )
      async def accept(
          self,
          interaction_act: discord.Interaction,
          button: discord.ui.Button,
      ):
        await interaction_act.response.send_message(
            f"✅ Bewerbung von {user.mention} wurde von"
            f" {interaction_act.user.mention} **ANGENOMMEN**!"
        )
        try:
          await user.send(
              "🎉 Deine Bewerbung bei der Army wurde **ANGENOMMEN**! Bitte"
              " finde dich im HQ zum Gespräch ein."
          )
        except Exception:
          pass
        log_action(
            "Bewerbung Angenommen",
            f"Bewerber: {self.ic_name.value}",
            interaction_act.user.display_name,
        )

      @discord.ui.button(
          label="Ablehnen",
          style=discord.ButtonStyle.red,
          custom_id="btn_reject_app",
      )
      async def reject(
          self,
          interaction_act: discord.Interaction,
          button: discord.ui.Button,
      ):
        await interaction_act.response.send_message(
            f"❌ Bewerbung von {user.mention} wurde von"
            f" {interaction_act.user.mention} **ABGELEHNT**."
        )
        try:
          await user.send("Leider wurde deine Bewerbung bei der Army **ABGELEHNT**.")
        except Exception:
          pass
        log_action(
            "Bewerbung Ablehnt",
            f"Bewerber: {self.ic_name.value}",
            interaction_act.user.display_name,
        )

    if log_channel:
      await log_channel.send(embed=embed, view=BewerbungActionView())

    log_action("Bewerbung Eingereicht", f"IC: {self.ic_name.value}", user.name)

    await interaction.response.send_message(
        "✅ Deine Bewerbung wurde erfolgreich eingereicht und wird von der"
        " Führungsebene geprüft!",
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

  try:
    synced = await bot.tree.sync()
    print(f"Synced {len(synced)} slash commands.")
  except Exception as e:
    print(e)

  if not tages_aufstellung_task.is_running():
    tages_aufstellung_task.start()

  print(f"Eingeloggt als {bot.user}")


# --- NEW & UPDATED COMMANDS ---
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
bot.run(TOKEN)
