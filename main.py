import discord
from discord.ext import commands, tasks
from discord.ui import View, Button, Modal, Select, TextInput
from datetime import datetime, timedelta
import asyncio

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

active_obzvons = {}
reports = {}
user_warnings = {}  # Добавлено отсутствующее объявление

# Каналы для логирования
LOG_CHANNELS = {
    "forms": "формы-наказаний",
    "messages": "сообщения",
    "users": "пользователи",
    "voice": "голосовые-каналы",
    "reports": "репорт",
    "private": "приватные-комнаты",
    "calls": "обзвоны",
    "auto_punish": "авто-наказания",
    "moderators": "модераторы",
    "economy": "экономика"
}

# Хранилище настроек серверов
server_settings = {}

# Роли для выдачи (по умолчанию)
AVAILABLE_ROLES = [
    "Новичок", "Участник", "Активный", "VIP", "Модератор", "Администратор"
]


class ReportCreateModal(Modal):
    def __init__(self, channel):
        super().__init__(title="Создать репорт")
        self.channel = channel
        self.description = TextInput(
            label="Описание проблемы",
            style=discord.TextStyle.paragraph,
            placeholder="Опишите детально что произошло...",
            required=True
        )
        self.add_item(self.description)

    async def on_submit(self, interaction: discord.Interaction):
        report_id = f"report-{int(datetime.utcnow().timestamp())}"

        embed = discord.Embed(title="🚨 Новый репорт",
                              description=self.description.value,
                              color=discord.Color.red(),
                              timestamp=datetime.utcnow())
        embed.add_field(name="Автор",
                        value=interaction.user.mention,
                        inline=True)
        embed.add_field(name="Канал", value=self.channel.mention, inline=True)
        embed.add_field(name="ID репорта", value=report_id, inline=False)

        # Отправляем в канал репортов
        report_channel = discord.utils.get(interaction.guild.text_channels,
                                           name="репорт")
        if report_channel:
            view = ReportActionView(report_id, interaction.user, self.channel)
            await report_channel.send(embed=embed, view=view)

        await interaction.response.send_message(
            "✅ Репорт отправлен модераторам!", ephemeral=True)


class ReportActionView(View):
    def __init__(self, report_id, author, channel):
        super().__init__(timeout=None)
        self.report_id = report_id
        self.author = author
        self.channel = channel

    @discord.ui.button(label="Принять",
                       style=discord.ButtonStyle.success,
                       emoji="✅")
    async def accept_report(self, interaction: discord.Interaction,
                            button: Button):
        embed = discord.Embed(
            title="✅ Репорт принят",
            description=
            f"Репорт {self.report_id} принят модератором {interaction.user.mention}",
            color=discord.Color.green())
        await interaction.response.edit_message(embed=embed, view=None)

        # Логируем в канал модераторов
        await log_action(
            "moderators", interaction.guild,
            f"🟢 Репорт {self.report_id} принят модератором {interaction.user.mention}"
        )

    @discord.ui.button(label="Отклонить",
                       style=discord.ButtonStyle.danger,
                       emoji="❌")
    async def decline_report(self, interaction: discord.Interaction,
                             button: Button):
        embed = discord.Embed(
            title="❌ Репорт отклонён",
            description=
            f"Репорт {self.report_id} отклонён модератором {interaction.user.mention}",
            color=discord.Color.red())
        await interaction.response.edit_message(embed=embed, view=None)

        # Логируем в канал модераторов
        await log_action(
            "moderators", interaction.guild,
            f"🔴 Репорт {self.report_id} отклонён модератором {interaction.user.mention}"
        )


class RoleSelectView(View):
    def __init__(self, guild):
        super().__init__(timeout=30)
        self.add_item(RoleSelect(guild))


class RoleSelect(Select):
    def __init__(self, guild):
        # Получаем все роли сервера, кроме @everyone и ботовских
        server_roles = [
            role for role in guild.roles
            if role.name != "@everyone" and not role.managed
        ]

        # Ограничиваем до 25 ролей (лимит Discord Select)
        server_roles = server_roles[:25]

        options = [
            discord.SelectOption(label=role.name,
                                 value=str(role.id),
                                 description=f"Позиция {role.position}")
            for role in server_roles
        ]

        if not options:
            options = [
                discord.SelectOption(label="Нет доступных ролей", value="none")
            ]

        super().__init__(placeholder="Выберите роль для выдачи",
                         options=options)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message(
                "❌ На сервере нет доступных ролей для выдачи.", ephemeral=True)
            return

        role_id = int(self.values[0])
        role = interaction.guild.get_role(role_id)

        if not role:
            await interaction.response.send_message("❌ Роль не найдена.",
                                                    ephemeral=True)
            return

        await interaction.response.send_message(
            f"Выберите пользователя для выдачи роли `{role.name}`",
            view=UserSelectView(role),
            ephemeral=True)


class UserSelectView(View):
    def __init__(self, role):
        super().__init__(timeout=30)
        self.role = role
        self.add_item(UserSelect(role))


class UserSelect(Select):
    def __init__(self, role):
        # Создаем простые опции для примера
        options = [
            discord.SelectOption(label="Ввести ID или упоминание",
                                 value="manual_input",
                                 description="Ввести пользователя вручную")
        ]
        super().__init__(placeholder="Выберите способ",
                         options=options,
                         min_values=1,
                         max_values=1)
        self.role = role

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(UserInputModal(self.role))


class UserInputModal(Modal):
    def __init__(self, role):
        super().__init__(title="Выдача роли")
        self.role = role
        self.user_input = TextInput(
            label="ID или упоминание пользователя",
            placeholder="123456789012345678 или @username",
            required=True
        )
        self.add_item(self.user_input)

    async def on_submit(self, interaction: discord.Interaction):
        user_str = self.user_input.value.strip()

        # Пытаемся найти пользователя
        member = None
        if user_str.startswith('<@') and user_str.endswith('>'):
            user_id = user_str[2:-1].replace('!', '')
            member = interaction.guild.get_member(int(user_id))
        elif user_str.isdigit():
            member = interaction.guild.get_member(int(user_str))
        else:
            member = discord.utils.get(interaction.guild.members,
                                       name=user_str)

        if not member:
            await interaction.response.send_message(
                "❌ Пользователь не найден.", ephemeral=True)
            return

        try:
            await member.add_roles(self.role)
            embed = discord.Embed(
                title="✅ Роль выдана",
                description=
                f"Роль `{self.role.name}` выдана пользователю {member.mention}",
                color=discord.Color.green())
            embed.add_field(name="Модератор", value=interaction.user.mention)
            await interaction.response.send_message(embed=embed)

            # Логируем выдачу роли
            await log_action(
                "moderators", interaction.guild,
                f"🎭 {interaction.user.mention} выдал роль `{self.role.name}` пользователю {member.mention}"
            )

        except Exception as e:
            await interaction.response.send_message(
                f"❌ Ошибка при выдаче роли: {str(e)}", ephemeral=True)


async def log_action(log_type, guild, message):
    """Функция для логирования действий"""
    if log_type in LOG_CHANNELS:
        channel_name = LOG_CHANNELS[log_type]
        channel = discord.utils.get(guild.text_channels, name=channel_name)

        # Если канал не найден, пытаемся создать его
        if not channel:
            try:
                # Создаем категорию для логов если её нет
                log_category = discord.utils.get(guild.categories,
                                                 name="📊 Логи")
                if not log_category:
                    log_category = await guild.create_category("📊 Логи")

                # Создаем канал логирования
                channel = await guild.create_text_channel(
                    channel_name,
                    category=log_category,
                    topic=f"Автоматическое логирование {log_type}")
                print(f"✅ Создан канал логирования {channel_name}")
            except Exception as e:
                print(f"❌ Не удалось создать канал {channel_name}: {e}")
                return

        if channel:
            try:
                embed = discord.Embed(description=message,
                                      color=discord.Color.blue(),
                                      timestamp=datetime.utcnow())
                embed.set_footer(text=f"Тип лога: {log_type}")
                await channel.send(embed=embed)
            except Exception as e:
                print(f"❌ Ошибка отправки лога в {channel_name}: {e}")


@bot.tree.command(name="репорт",
                  description="Создать репорт по текущему каналу")
async def create_report(interaction: discord.Interaction):
    await interaction.response.send_modal(
        ReportCreateModal(interaction.channel))


@bot.tree.command(name="роль", description="Выдать роль пользователю")
async def give_role(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_roles:
        await interaction.response.send_message(
            "❌ У вас нет прав на выдачу ролей.", ephemeral=True)
        return

    await interaction.response.send_message("Выберите роль для выдачи",
                                            view=RoleSelectView(
                                                interaction.guild),
                                            ephemeral=True)


@bot.tree.command(name="создать_обзвон_бот",
                  description="Создать обзвон как BLACK CHANNEL BOT")
async def create_bot_call(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Обзвон",
        description=
        "Вы можете создать специальную категорию со всеми необходимыми каналами и требуемым функционалом для удобного проведения обзвонов.",
        color=0x2b2d31)

    view = View()
    create_button = Button(label="Создать обзвон",
                           style=discord.ButtonStyle.success)

    async def create_callback(button_interaction):
        await button_interaction.response.send_modal(CreateObzvonModal())

    create_button.callback = create_callback
    view.add_item(create_button)

    await interaction.response.send_message(embed=embed, view=view)


class CreateObzvonModal(Modal):
    def __init__(self):
        super().__init__(title="Создание обзвона")
        self.name = TextInput(
            label="Название обзвона", 
            placeholder="Например Лидеры",
            required=True
        )
        self.add_item(self.name)

    async def on_submit(self, interaction: discord.Interaction):
        name = self.name.value
        guild = interaction.guild

        try:
            category = await guild.create_category(f"Обзвон на {name}")

            role_wait = await guild.create_role(name="Ожидание обзвона")
            role_call = await guild.create_role(name="Проходит обзвон")
            role_end = await guild.create_role(name="Итоги")

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                role_wait: discord.PermissionOverwrite(connect=True, view_channel=True),
                role_call: discord.PermissionOverwrite(connect=True, view_channel=True),
                role_end: discord.PermissionOverwrite(connect=True, view_channel=True)
            }

            ch1 = await guild.create_voice_channel("🌑 Ожидание Обзвона",
                                                   category=category,
                                                   overwrites=overwrites)
            ch2 = await guild.create_voice_channel("🌓 Проходит Обзвон",
                                                   category=category,
                                                   overwrites=overwrites)
            ch3 = await guild.create_voice_channel("🌕 Ожидание итогов",
                                                   category=category,
                                                   overwrites=overwrites)

            text_channel = await guild.create_text_channel("📋 настройки обзвона",
                                                           category=category)
            await text_channel.send(view=ObzvonControlView(
                role_wait, role_call, role_end, [ch1, ch2, ch3], category))

            active_obzvons[category.id] = {
                "timestamp": datetime.utcnow(),
                "channels": [ch1, ch2, ch3],
                "roles": [role_wait, role_call, role_end],
                "category": category,
                "text_channel": text_channel
            }

            await interaction.response.send_message(f"Обзвон {name} создан!",
                                                    ephemeral=True)

            # Логируем создание обзвона
            await log_action(
                "calls", guild,
                f"📞 {interaction.user.mention} создал обзвон {name}")
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Ошибка при создании обзвона: {str(e)}", ephemeral=True)


class CreateObzvonView(View):
    @discord.ui.button(label="Создать обзвон", style=discord.ButtonStyle.green)
    async def create_obzvon(self, interaction: discord.Interaction,
                            button: Button):
        await interaction.response.send_modal(CreateObzvonModal())


class MoveSelectView(View):
    def __init__(self, members, role, channel):
        super().__init__(timeout=30)
        self.add_item(MoveSelect(members, role, channel))


class MoveSelect(Select):
    def __init__(self, members, role, channel):
        options = [
            discord.SelectOption(label=member.display_name,
                                 value=str(member.id))
            for member in members[:25]
        ]
        super().__init__(placeholder="Выберите пользователя", options=options)
        self.role = role
        self.channel = channel

    async def callback(self, interaction: discord.Interaction):
        member = interaction.guild.get_member(int(self.values[0]))
        if member:
            try:
                for r in interaction.guild.roles:
                    if r.name in ["Ожидание обзвона", "Проходит обзвон", "Итоги"]:
                        await member.remove_roles(r)
                await member.add_roles(self.role)
                if member.voice:
                    await member.move_to(self.channel)
                await interaction.response.send_message(
                    f"✅ {member.mention} перемещён в {self.channel.name} и получил роль `{self.role.name}`",
                    ephemeral=True)

                # Логируем перемещение в обзвоне
                await log_action(
                    "calls", interaction.guild,
                    f"🔄 {interaction.user.mention} переместил {member.mention} в {self.channel.name}"
                )
            except Exception as e:
                await interaction.response.send_message(
                    f"❌ Ошибка при перемещении: {str(e)}", ephemeral=True)
        else:
            await interaction.response.send_message("⛔ Участник не найден",
                                                    ephemeral=True)


class ObzvonControlView(View):
    def __init__(self, role_wait, role_call, role_end, voice_channels,
                 category):
        super().__init__(timeout=None)
        self.role_wait = role_wait
        self.role_call = role_call
        self.role_end = role_end
        self.voice_channels = voice_channels
        self.category = category

    @discord.ui.button(label="Переместить в Ожидание",
                       style=discord.ButtonStyle.primary)
    async def move_to_wait(self, interaction: discord.Interaction,
                           button: Button):
        members = [m for m in interaction.guild.members if not m.bot]
        await interaction.response.send_message("Выберите участника",
                                                view=MoveSelectView(
                                                    members, self.role_wait,
                                                    self.voice_channels[0]),
                                                ephemeral=True)

    @discord.ui.button(label="Переместить в Проходит",
                       style=discord.ButtonStyle.success)
    async def move_to_call(self, interaction: discord.Interaction,
                           button: Button):
        members = [m for m in interaction.guild.members if not m.bot]
        await interaction.response.send_message("Выберите участника",
                                                view=MoveSelectView(
                                                    members, self.role_call,
                                                    self.voice_channels[1]),
                                                ephemeral=True)

    @discord.ui.button(label="Переместить в Итоги",
                       style=discord.ButtonStyle.secondary)
    async def move_to_end(self, interaction: discord.Interaction,
                          button: Button):
        members = [m for m in interaction.guild.members if not m.bot]
        await interaction.response.send_message("Выберите участника",
                                                view=MoveSelectView(
                                                    members, self.role_end,
                                                    self.voice_channels[2]),
                                                ephemeral=True)

    @discord.ui.button(label="Завершить обзвон",
                       style=discord.ButtonStyle.danger)
    async def end_obzvon(self, interaction: discord.Interaction,
                         button: Button):
        data = active_obzvons.get(self.category.id)
        if data:
            try:
                for ch in data["channels"]:
                    await ch.delete()
                for role in data["roles"]:
                    await role.delete()
                await data["text_channel"].delete()
                await data["category"].delete()
                del active_obzvons[self.category.id]
                await interaction.response.send_message("Обзвон удалён.",
                                                        ephemeral=True)

                # Логируем завершение обзвона
                await log_action("calls", interaction.guild,
                                 f"🔚 {interaction.user.mention} завершил обзвон")
            except Exception as e:
                await interaction.response.send_message(
                    f"❌ Ошибка при удалении обзвона: {str(e)}", ephemeral=True)


class ReportActionButtonsView(View):
    def __init__(self, report_id, target, reporter):
        super().__init__(timeout=None)
        self.report_id = report_id
        self.target = target
        self.reporter = reporter

    @discord.ui.button(label="Одобрить",
                       style=discord.ButtonStyle.success,
                       emoji="✅")
    async def approve_report(self, interaction: discord.Interaction,
                             button: Button):
        if not interaction.user.guild_permissions.moderate_members:
            await interaction.response.send_message(
                "❌ У вас нет прав для обработки жалоб.", ephemeral=True)
            return

        embed = discord.Embed(
            title="✅ Жалоба одобрена",
            description=
            f"Жалоба на {self.target.mention} одобрена модератором {interaction.user.mention}",
            color=discord.Color.green(),
            timestamp=datetime.utcnow())

        # Убираем кнопки
        await interaction.response.edit_message(embed=embed, view=None)

        # Удаляем из активных жалоб
        if self.report_id in reports:
            del reports[self.report_id]

        # Логируем решение
        await log_action(
            "reports", interaction.guild,
            f"✅ {interaction.user.mention} одобрил жалобу на {self.target.mention}"
        )

    @discord.ui.button(label="Отклонить",
                       style=discord.ButtonStyle.danger,
                       emoji="❌")
    async def decline_report(self, interaction: discord.Interaction,
                             button: Button):
        if not interaction.user.guild_permissions.moderate_members:
            await interaction.response.send_message(
                "❌ У вас нет прав для обработки жалоб.", ephemeral=True)
            return

        embed = discord.Embed(
            title="❌ Жалоба отклонена",
            description=
            f"Жалоба на {self.target.mention} отклонена модератором {interaction.user.mention}",
            color=discord.Color.red(),
            timestamp=datetime.utcnow())

        # Убираем кнопки
        await interaction.response.edit_message(embed=embed, view=None)

        # Удаляем из активных жалоб
        if self.report_id in reports:
            del reports[self.report_id]

        # Логируем решение
        await log_action(
            "reports", interaction.guild,
            f"❌ {interaction.user.mention} отклонил жалобу на {self.target.mention}"
        )


class ReportModal(Modal):
    def __init__(self, target):
        super().__init__(title="Жалоба на участника")
        self.target = target
        self.reason = TextInput(
            label="Причина", 
            style=discord.TextStyle.paragraph,
            required=True
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        report_id = f"{interaction.guild_id}-{interaction.user.id}-{int(datetime.utcnow().timestamp())}"
        reports[report_id] = {
            "target": self.target,
            "reason": self.reason.value,
            "reporter": interaction.user,
            "timestamp": datetime.utcnow()
        }

        embed = discord.Embed(title="🚨 Новая жалоба",
                              color=discord.Color.red(),
                              timestamp=datetime.utcnow())
        embed.add_field(name="На пользователя",
                        value=self.target.mention,
                        inline=True)
        embed.add_field(name="От пользователя",
                        value=interaction.user.mention,
                        inline=True)
        embed.add_field(name="ID жалобы", value=report_id, inline=False)
        embed.add_field(name="Причина", value=self.reason.value, inline=False)

        await interaction.response.send_message(
            "✅ Жалоба отправлена модераторам!", ephemeral=True)

        # Ищем канал для жалоб
        report_channel = None
        for channel_name in ["жалобы", "репорт", "reports"]:
            report_channel = discord.utils.get(interaction.guild.text_channels,
                                               name=channel_name)
            if report_channel:
                break

        if report_channel:
            view = ReportActionButtonsView(report_id, self.target,
                                           interaction.user)
            await report_channel.send(embed=embed, view=view)
        else:
            # Если канала нет, пытаемся создать
            try:
                report_channel = await interaction.guild.create_text_channel(
                    "жалобы")
                view = ReportActionButtonsView(report_id, self.target,
                                               interaction.user)
                await report_channel.send(embed=embed, view=view)
            except Exception as e:
                print(f"❌ Не удалось создать канал жалоб: {e}")

        # Логируем жалобу
        await log_action(
            "reports", interaction.guild,
            f"📋 {interaction.user.mention} подал жалобу на {self.target.mention}. Причина: {self.reason.value}"
        )


@bot.tree.command(name="варн", description="Выдать предупреждение участнику")
async def warn(interaction: discord.Interaction,
               member: discord.Member,
               reason: str = "Не указана"):
    if not interaction.user.guild_permissions.moderate_members:
        await interaction.response.send_message(
            "❌ У вас нет прав для выдачи предупреждений.", ephemeral=True)
        return

    if member.id not in user_warnings:
        user_warnings[member.id] = 0

    user_warnings[member.id] += 1
    warnings_count = user_warnings[member.id]

    embed = discord.Embed(title="⚠️ Предупреждение",
                          color=discord.Color.orange())
    embed.add_field(name="Участник", value=member.mention)
    embed.add_field(name="Модератор", value=interaction.user.mention)
    embed.add_field(name="Причина", value=reason, inline=False)
    embed.add_field(name="Количество предупреждений",
                    value=f"{warnings_count}/3")

    if warnings_count >= 3:
        try:
            await member.ban(
                reason=f"3 предупреждения. Последняя причина: {reason}")
            embed.add_field(name="Действие",
                            value="🔨 Забанен за 3 предупреждения",
                            inline=False)
            user_warnings[member.id] = 0

            # Логируем автобан
            await log_action(
                "auto_punish", interaction.guild,
                f"🔨 {member.mention} автоматически забанен за 3 предупреждения"
            )
        except Exception as e:
            embed.add_field(name="Ошибка",
                            value=f"Не удалось забанить пользователя: {str(e)}",
                            inline=False)

    await interaction.response.send_message(embed=embed)

    # Логируем предупреждение
    await log_action(
        "forms", interaction.guild,
        f"⚠️ {interaction.user.mention} выдал предупреждение {member.mention}. Причина: {reason}"
    )


@bot.tree.command(name="снять_варн",
                  description="Снять предупреждение у участника")
async def remove_warn(interaction: discord.Interaction,
                      member: discord.Member):
    if not interaction.user.guild_permissions.moderate_members:
        await interaction.response.send_message(
            "❌ У вас нет прав для снятия предупреждений.", ephemeral=True)
        return

    if member.id not in user_warnings or user_warnings[member.id] == 0:
        await interaction.response.send_message(
            f"У {member.mention} нет предупреждений.", ephemeral=True)
        return

    user_warnings[member.id] -= 1
    warnings_count = user_warnings[member.id]

    embed = discord.Embed(title="✅ Предупреждение снято",
                          color=discord.Color.green())
    embed.add_field(name="Участник", value=member.mention)
    embed.add_field(name="Модератор", value=interaction.user.mention)
    embed.add_field(name="Осталось предупреждений",
                    value=f"{warnings_count}/3")

    await interaction.response.send_message(embed=embed)

    # Логируем снятие предупреждения
    await log_action(
        "forms", interaction.guild,
        f"✅ {interaction.user.mention} снял предупреждение с {member.mention}")


@bot.tree.command(name="кик", description="Исключить участника с сервера")
async def kick(interaction: discord.Interaction,
               member: discord.Member,
               reason: str = "Не указана"):
    if not interaction.user.guild_permissions.kick_members:
        await interaction.response.send_message(
            "❌ У вас нет прав для исключения участников.", ephemeral=True)
        return

    try:
        await member.kick(reason=reason)
        embed = discord.Embed(title="👟 Участник исключён",
                              color=discord.Color.orange())
        embed.add_field(name="Участник", value=member.mention)
        embed.add_field(name="Модератор", value=interaction.user.mention)
        embed.add_field(name="Причина", value=reason, inline=False)
        await interaction.response.send_message(embed=embed)

        # Логируем кик
        await log_action(
            "forms", interaction.guild,
            f"👟 {interaction.user.mention} исключил {member.mention}. Причина: {reason}"
        )
    except Exception as e:
        await interaction.response.send_message(
            f"❌ Не удалось исключить участника: {str(e)}", ephemeral=True)


@bot.tree.command(name="бан", description="Заблокировать участника навсегда")
async def ban(interaction: discord.Interaction,
              member: discord.Member,
              reason: str = "Не указана"):
    if not interaction.user.guild_permissions.ban_members:
        await interaction.response.send_message(
            "❌ У вас нет прав для блокировки участников.", ephemeral=True)
        return

    try:
        await member.ban(reason=reason)
        if member.id in user_warnings:
            user_warnings[member.id] = 0
        embed = discord.Embed(title="🔨 Участник заблокирован",
                              color=discord.Color.red())
        embed.add_field(name="Участник", value=member.mention)
        embed.add_field(name="Модератор", value=interaction.user.mention)
        embed.add_field(name="Причина", value=reason, inline=False)
        await interaction.response.send_message(embed=embed)

        # Логируем бан
        await log_action(
            "forms", interaction.guild,
            f"🔨 {interaction.user.mention} заблокировал {member.mention}. Причина: {reason}"
        )
    except Exception as e:
        await interaction.response.send_message(
            f"❌ Не удалось заблокировать участника: {str(e)}", ephemeral=True)


@bot.tree.command(name="мут", description="Заглушить участника на 5 минут")
async def mute(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.moderate_members:
        await interaction.response.send_message(
            "❌ У вас нет прав для выдачи мута.", ephemeral=True)
        return

    duration = timedelta(minutes=5)
    try:
        await member.timeout(until=datetime.utcnow() + duration)
        await interaction.response.send_message(
            f"🔇 {member.mention} получил мут на 5 минут.")

        # Логируем мут
        await log_action(
            "forms", interaction.guild,
            f"🔇 {interaction.user.mention} выдал мут {member.mention} на 5 минут"
        )
    except Exception as e:
        await interaction.response.send_message(
            f"❌ Не удалось выдать мут: {str(e)}", ephemeral=True)


@bot.tree.command(name="снять", description="Снять мут у участника")
async def unmute(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.moderate_members:
        await interaction.response.send_message(
            "❌ У вас нет прав для снятия мута.", ephemeral=True)
        return

    try:
        await member.timeout(until=None)
        await interaction.response.send_message(
            f"🔊 Мут снят с {member.mention}.")

        # Логируем снятие мута
        await log_action(
            "forms", interaction.guild,
            f"🔊 {interaction.user.mention} снял мут с {member.mention}")
    except Exception as e:
        await interaction.response.send_message(
            f"❌ Не удалось снять мут: {str(e)}", ephemeral=True)


@bot.tree.command(name="жалоба", description="Подать жалобу на участника")
async def report_command(interaction: discord.Interaction,
                         member: discord.Member):
    if member == interaction.user:
        await interaction.response.send_message(
            "Вы не можете пожаловаться на себя!", ephemeral=True)
    else:
        await interaction.response.send_modal(ReportModal(member))


@bot.tree.command(name="жалобы", description="Посмотреть все активные жалобы")
async def view_reports(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.moderate_members:
        await interaction.response.send_message(
            "❌ У вас нет прав для просмотра жалоб.", ephemeral=True)
        return

    if not reports:
        await interaction.response.send_message("📋 Активных жалоб нет.",
                                                ephemeral=True)
        return

    embed = discord.Embed(title="📋 Активные жалобы",
                          color=discord.Color.blue())

    for report_id, report_data in list(reports.items())[:10]:
        target = report_data["target"]
        reporter = report_data["reporter"]
        reason = report_data["reason"]
        timestamp = report_data["timestamp"].strftime("%d.%m.%Y %H:%M")

        embed.add_field(
            name=f"Жалоба на {target.display_name}",
            value=
            f"От: {reporter.display_name}\nПричина: {reason}\nВремя: {timestamp}",
            inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="обзвон",
                  description="Создание обзвона с каналами и ролями")
async def create_call(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Создание обзвона",
        description="Нажмите кнопку ниже, чтобы начать обзвон.",
        color=discord.Color.blue())
    await interaction.response.send_message(embed=embed,
                                            view=CreateObzvonView(),
                                            ephemeral=True)


class VerificationView(View):
    def __init__(self, verification_roles=None):
        super().__init__(timeout=None)
        self.verification_roles = verification_roles or []

    @discord.ui.button(label="✅ Верифицироваться",
                       style=discord.ButtonStyle.success,
                       emoji="✅")
    async def verify_user(self, interaction: discord.Interaction,
                          button: Button):
        guild = interaction.guild
        guild_id = guild.id

        # Получаем настройки сервера
        if guild_id not in server_settings:
            await interaction.response.send_message(
                "❌ Верификация не настроена на этом сервере.", ephemeral=True)
            return

        # Проверяем, есть ли настроенные роли для верификации
        verification_roles = server_settings[guild_id].get(
            "verification_roles", [])
        if not verification_roles:
            await interaction.response.send_message(
                "❌ Роли верификации не настроены.", ephemeral=True)
            return

        # Если есть несколько ролей, показываем выбор
        if len(verification_roles) > 1:
            # Показываем выбор ролей
            await interaction.response.send_message(
                "Выберите роль для получения",
                view=VerificationRoleSelectView(verification_roles, guild),
                ephemeral=True)
        else:
            # Если роль одна, выдаем её сразу
            role_id = verification_roles[0]
            role = guild.get_role(role_id)
            if role:
                try:
                    await interaction.user.add_roles(role)
                    await interaction.response.send_message(
                        f"✅ Вы успешно верифицированы! Вам выдана роль {role.mention}",
                        ephemeral=True)
                    await log_action(
                        "users", guild,
                        f"✅ {interaction.user.mention} прошел верификацию и получил роль {role.mention}"
                    )
                except Exception as e:
                    await interaction.response.send_message(
                        f"❌ Ошибка при выдаче роли: {str(e)}", ephemeral=True)
            else:
                await interaction.response.send_message(
                    "❌ Роль верификации не найдена.", ephemeral=True)


class VerificationRoleSelectView(View):
    def __init__(self, role_ids, guild):
        super().__init__(timeout=60)
        self.role_ids = role_ids
        self.add_item(VerificationRoleSelect(role_ids, guild))


class VerificationRoleSelect(Select):
    def __init__(self, role_ids, guild):
        options = []
        for role_id in role_ids[:25]:
            role = guild.get_role(role_id)
            if role:
                options.append(
                    discord.SelectOption(
                        label=role.name,
                        value=str(role.id),
                        description=f"Получить роль {role.name}"))

        if not options:
            options = [
                discord.SelectOption(label="Нет доступных ролей", value="none")
            ]

        super().__init__(placeholder="Выберите роль для верификации",
                         options=options,
                         min_values=1,
                         max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message(
                "❌ Нет доступных ролей для верификации.", ephemeral=True)
            return

        role_id = int(self.values[0])
        role = interaction.guild.get_role(role_id)

        if not role:
            await interaction.response.send_message("❌ Роль не найдена.",
                                                    ephemeral=True)
            return

        try:
            await interaction.user.add_roles(role)
            await interaction.response.edit_message(
                content=
                f"✅ Вы успешно верифицированы! Вам выдана роль {role.mention}",
                view=None)

            # Логируем верификацию
            await log_action(
                "users", interaction.guild,
                f"✅ {interaction.user.mention} прошел верификацию и получил роль {role.mention}"
            )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Ошибка при выдаче роли: {str(e)}", ephemeral=True)


# Очистка неактивных обзвонов
@tasks.loop(minutes=10)
async def cleanup_inactive():
    now = datetime.utcnow()
    to_delete = []
    for cat_id, data in active_obzvons.items():
        if now - data["timestamp"] > timedelta(hours=1):
            try:
                for ch in data["channels"]:
                    await ch.delete()
                for role in data["roles"]:
                    await role.delete()
                await data["text_channel"].delete()
                await data["category"].delete()
                to_delete.append(cat_id)
            except Exception as e:
                print(f"❌ Ошибка при очистке обзвона {cat_id}: {e}")
    for cat_id in to_delete:
        del active_obzvons[cat_id]


@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        cleanup_inactive.start()
        print(f"✅ Бот {bot.user} запущен!")
        print(f"🆔 ID бота: {bot.user.id}")
        print(f"📊 Подключен к {len(bot.guilds)} серверам")
        print(f"🚀 Синхронизировано {len(synced)} команд")
        print("📋 Доступные команды:")
        for command in bot.tree.get_commands():
            print(f"  - /{command.name}: {command.description}")
    except Exception as e:
        print(f"❌ Ошибка при запуске: {e}")


@bot.event
async def on_error(event, *args, **kwargs):
    print(f"❌ Ошибка в событии {event}: {args} {kwargs}")


@bot.event
async def on_command_error(ctx, error):
    print(f"❌ Ошибка команды: {error}")


@bot.command(name="say")
async def say(ctx, *, message):
    """Команда для отправки сообщения от имени бота"""
    try:
        await ctx.message.delete()
    except:
        pass
    await ctx.send(message)


if __name__ == "__main__":
    # Убедитесь, что токен правильный (без лишней буквы Y в начале)
    TOKEN = "MTMzMzM1MDY4NTQxMjAzNjYzOA.G_qKSB.rZ6EuRxg3Tc_EjmI6nTNeS1fBz4Q1lwr3xAdPc"
    
    try:
        bot.run(TOKEN)
    except Exception as e:
        print(f"❌ Критическая ошибка при запуске бота: {e}")
