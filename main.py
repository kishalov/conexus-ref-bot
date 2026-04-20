import os
import asyncio
import gspread
from datetime import datetime
from dotenv import load_dotenv
from oauth2client.service_account import ServiceAccountCredentials
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder

load_dotenv()

# --- КОНФИГУРАЦИЯ ---
TOKEN = os.getenv("BOT_TOKEN")               # Твой бот (админка)
MAIN_BOT_TOKEN = os.getenv("MAIN_BOT_TOKEN")   # Основной бот (клиентский)

# Читаем список админов из .env
raw_admins = os.getenv("ADMIN_IDS", os.getenv("ADMIN_ID", "0"))
ADMIN_IDS = [int(id.strip()) for id in raw_admins.split(",") if id.strip().isdigit()]

SHEET_NAME = "conexus-ref-bot"
MAIN_BOT_USERNAME = "Conexus_Crypto_Africa_ManagerBot"

bot = Bot(token=TOKEN)
main_bot_client = Bot(token=MAIN_BOT_TOKEN)
dp = Dispatcher()

# --- НАСТРОЙКА GOOGLE TABLES ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("service_account.json", scope)
gc = gspread.authorize(creds)
sheet = gc.open(SHEET_NAME)

users_sheet = sheet.worksheet("users")
rewards_sheet = sheet.worksheet("referral_rewards")

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (ПОЛНЫЕ) ---
def get_main_kb(user_id):
    builder = ReplyKeyboardBuilder()
    builder.button(text="👤 Мой профиль")
    if user_id in ADMIN_IDS:
        builder.button(text="🔑 Админка")
    return builder.as_markup(resize_keyboard=True)

def get_user_by_tg(tg_id):
    try:
        cell = users_sheet.find(str(tg_id), in_column=2)
        if cell:
            values = users_sheet.row_values(cell.row)
            # Возвращаем полный словарь данных из таблицы
            return {
                "id": values[0], 
                "tg_id": values[1], 
                "username": values[2], 
                "referrer_id": values[3], 
                "balance": values[4] if len(values) > 4 else 0, 
                "registration_date": values[5] if len(values) > 5 else ""
            }
    except: return None
    return None

# --- ОБРАБОТЧИКИ ---

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or "Unknown"
    args = message.text.split()
    
    existing_user = get_user_by_tg(user_id)
    
    # Логика регистрации нового пользователя
    if not existing_user:
        referrer_internal_id = "" 
        if len(args) > 1 and args[1].startswith("ref_"):
            ref_tg_id = args[1].replace("ref_", "")
            if ref_tg_id.isdigit() and int(ref_tg_id) != user_id:
                ref_obj = get_user_by_tg(ref_tg_id)
                if ref_obj:
                    referrer_internal_id = ref_obj["id"]

        try:
            all_users = users_sheet.get_all_values()
            new_id = len(all_users) 
            now = datetime.now().strftime("%d.%m.%Y %H:%M")
            users_sheet.append_row([new_id, str(user_id), username, str(referrer_internal_id), 0, now])
            print(f"Успешная регистрация: {username}")
        except Exception as e:
            print(f"Ошибка записи в Google Таблицу: {e}")
    
    # Ответ пользователю (только если он пишет в ТВОЙ бот)
    if message.bot.token == TOKEN:
        await message.answer(
            "Добро пожаловать в реферальную систему Conexus!", 
            reply_markup=get_main_kb(user_id)
        )

@dp.message(Command("deal"))
async def process_deal(message: types.Message):
    if message.from_user.id not in ADMIN_IDS: return
    
    args = message.text.split()
    if len(args) < 3:
        await message.answer("Использование: /deal [TG_ID_клиента] [сумма]")
        return

    client_tg_id = args[1]
    amount = args[2]
    
    client_data = get_user_by_tg(client_tg_id)
    if not client_data:
        await message.answer("Ошибка: клиент не найден в базе данных.")
        return

    if not client_data["referrer_id"]:
        await message.answer("У этого клиента нет пригласителя. Бонус не начислен.")
        return

    try:
        reward = float(amount) * 0.1
        new_reward_id = len(rewards_sheet.get_all_values())
        now = datetime.now().strftime("%d.%m.%Y %H:%M")
        
        # Запись в лист наград: ID, Реферер, Клиент, Сумма, Дата, Статус, Тип
        rewards_sheet.append_row([
            new_reward_id, 
            client_data["referrer_id"], 
            client_data["id"], 
            reward, 
            now, 
            'pending', 
            'SALE'
        ])
        await message.answer(f"✅ Сделка на {amount}р подтверждена.\nРефереру (ID: {client_data['referrer_id']}) начислено {reward}р (ожидает выплаты).")
    except Exception as e:
        await message.answer(f"Техническая ошибка при записи сделки: {e}")

@dp.message(F.text.contains("Мой профиль"))
async def profile_view(message: types.Message):
    user_id = message.from_user.id
    user = get_user_by_tg(user_id)
    if not user: 
        await message.answer("Пожалуйста, сначала используйте /start")
        return

    try:
        all_rewards = rewards_sheet.get_all_records()
        
        # Функция точного подсчета баланса с учетом формата чисел (запятые/точки)
        def safe_sum(status):
            total = 0
            for r in all_rewards:
                if str(r.get('referrer_id')) == str(user['id']) and r.get('status') == status:
                    try:
                        val = str(r.get('reward_amount', 0)).replace(',', '.')
                        total += float(val)
                    except: continue
            return int(total)

        pending = safe_sum('pending')
        paid = safe_sum('paid')
        
        all_users = users_sheet.get_all_records()
        friends_count = sum(1 for u in all_users if str(u.get('referrer_id')) == str(user['id']))

        # Ссылка напрямую на основной бот
        link = f"https://t.me/{MAIN_BOT_USERNAME}?start=ref_{user_id}"
        
        text = (f"<b>👤 Личный кабинет</b>\n\n"
                f"🔗 <b>Ваша ссылка для приглашения:</b>\n"
                f"<code>{link}</code>\n\n"
                f"📊 <b>Статистика:</b>\n"
                f"👥 Приглашено друзей: {friends_count}\n"
                f"✅ Выплачено за всё время: {paid}р\n"
                f"⏳ Ожидает выплаты: {pending}р")
        await message.answer(text, parse_mode="HTML")
    except Exception as e:
        print(f"Ошибка формирования профиля: {e}")
        await message.answer("Не удалось загрузить данные профиля.")

@dp.message(F.text.contains("Админка"))
async def admin_view(message: types.Message):
    if message.from_user.id not in ADMIN_IDS: return
    
    all_rewards = rewards_sheet.get_all_records()
    pending_rows = [r for r in all_rewards if r['status'] == 'pending']
    
    if not pending_rows:
        await message.answer("📭 На данный момент заявок на выплату нет.")
        return
    
    res = "🚀 <b>Список активных заявок:</b>\n\n"
    for r in pending_rows:
        res += f"🔹 <b>ID выплаты:</b> <code>{r['id']}</code>\n"
        res += f"🔹 Реферер (внутр. ID): <code>{r['referrer_id']}</code>\n"
        res += f"💰 Сумма: <b>{r['reward_amount']}р</b>\n"
        res += "------------------\n"
    
    res += "\nДля выплаты используйте: <code>/pay [ID]</code>"
    await message.answer(res, parse_mode="HTML")

@dp.message(Command("pay"))
async def process_pay(message: types.Message):
    if message.from_user.id not in ADMIN_IDS: return
    args = message.text.split()
    if len(args) < 2: 
        await message.answer("Укажите ID выплаты: /pay 5")
        return
    
    reward_id = args[1]
    
    try:
        # Поиск записи о выплате
        cell = rewards_sheet.find(str(reward_id), in_column=1)
        if cell:
            rewards_sheet.update_cell(cell.row, 6, 'paid') # Столбец Status
            row_data = rewards_sheet.row_values(cell.row)
            ref_internal_id = row_data[1]
            amount = row_data[3]
            
            # Поиск TG_ID реферера для уведомления
            u_cell = users_sheet.find(str(ref_internal_id), in_column=1)
            if u_cell:
                ref_tg_id = users_sheet.cell(u_cell.row, 2).value
                try:
                    await bot.send_message(ref_tg_id, f"💰 <b>Хорошие новости!</b>\n\nВам выплачено вознаграждение в размере <b>{amount}р</b>.", parse_mode="HTML")
                except: pass # Если юзер заблокировал бота
                await message.answer(f"✅ Выплата #{reward_id} успешно проведена. Уведомление отправлено.")
        else:
            await message.answer("Заявка с таким ID не найдена.")
    except Exception as e:
        await message.answer(f"Ошибка при обработке выплаты: {e}")

# --- ФИНАЛЬНЫЙ БЛОК ЗАПУСКА С РЕШЕНИЕМ КОНФЛИКТА ---

async def main():
    print("--- Инициализация системы ---")
    
    # 1. ПРИНУДИТЕЛЬНАЯ ОЧИСТКА ДО ЗАПУСКА ПОЛЛИНГА
    try:
        print("Отключаем вебхуки...")
        await bot.delete_webhook(drop_pending_updates=True)
        await main_bot_client.delete_webhook(drop_pending_updates=True)
        await asyncio.sleep(1.5)
        print("Вебхуки успешно сброшены.")
    except Exception as e:
        print(f"Предупреждение при сбросе: {e}")

    print("--- Запуск прослушивания ботов ---")
    await dp.start_polling(bot, main_bot_client, skip_updates=True)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Бот остановлен.")