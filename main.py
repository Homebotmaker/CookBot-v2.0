import asyncio
import logging
import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
from recipes import RECIPES, calculate_recipe_cost

logging.basicConfig(level=logging.INFO)
bot = Bot(token=config.TG_BOT_API_KEY)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()

# Локальное runtime-хранилище в оперативной памяти
USER_DATA = {}

# Состояния машины FSM
class BotStates(StatesGroup):
    waiting_for_diet = State()
    waiting_for_gender = State()
    waiting_for_weight = State()
    waiting_for_height = State()
    waiting_for_age = State()
    waiting_for_activity = State()
    waiting_for_budget_period = State()
    waiting_for_budget_amount = State()
    waiting_for_store = State()
    
    waiting_for_bulk = State()
    waiting_for_qty = State()
    waiting_for_exp = State()

# Кнопки главного меню (с добавлением опций удаления, сброса и уведомлений)
main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📦 Наличие продуктов"), KeyboardButton(text="➕ Добавить продукт")],
        [KeyboardButton(text="📝 Закуп чек лист"), KeyboardButton(text="🍳 Предложить рецепт")],
        [KeyboardButton(text="🗑️ Удалить продукт"), KeyboardButton(text="🧹 Сбросить наличие")],
        [KeyboardButton(text="🔔 Настройка уведомлений")]
    ],
    resize_keyboard=True
)

# Вспомогательные утилиты классификации продуктов
def get_category(name: str) -> str:
    name = name.lower().strip()
    categories = {
        "Мясо и птица": ["мясо", "курица", "индейка", "говядина", "свинина", "фарш", "субпродукты"],
        "Рыба и морепродукты": ["рыба", "лосось", "треска", "креветки", "морепродукты"],
        "Молочные продукты": ["молоко", "творог", "сыр", "йогурт", "сметана", "сливки"],
        "Овощи и зелень": ["огурец", "огурцы", "помидор", "помидоры", "зелень", "укроп", "петрушка", "салат"],
        "Фрукты и ягоды": ["яблоко", "банан", "ягода", "клубника", "малина", "черника", "ягоды"],
        "Бакалея": ["крупа", "рис", "гречка", "макароны", "мука", "сахар", "соль", "масло", "овсянка", "вода"],
        "Соусы": ["майонез", "маргарин"]
    }
    for cat, keywords in categories.items():
        if any(k in name for k in keywords):
            return cat
    return "Другое"

def needs_expiration(name: str) -> bool:
    name = name.lower().strip()
    keywords = [
        "мясо", "птица", "курица", "индейка", "говядина", "свинина", "субпродукты", 
        "рыба", "морепродукты", "креветки", "молоко", "творог", "сыр", "йогурт", 
        "сметана", "яйца", "огурец", "огурцы", "помидор", "помидоры", "ягода", 
        "ягоды", "зелень", "майонез", "маргарин"
    ]
    return any(k in name for k in keywords)

def guess_unit(name: str) -> str:
    name = name.lower().strip()
    if any(k in name for k in ["молоко", "вода", "сливки", "масло"]):
        return "мл"
    if any(k in name for k in ["яйца", "яблоко", "огурец", "помидор", "банан"]):
        return "шт"
    return "грамм"

def calculate_kbzhu(gender: str, weight: float, height: float, age: int, activity_factor: float):
    if gender == "м":
        bmr = 10 * weight + 6.25 * height - 5 * age + 5
    else:
        bmr = 10 * weight + 6.25 * height - 5 * age - 161
    calories = int(bmr * activity_factor)
    proteins = int((calories * 0.3) / 4)
    fats = int((calories * 0.3) / 9)
    carbs = int((calories * 0.4) / 4)
    return calories, proteins, fats, carbs

# --- СТАРТ И НАСТРОЙКА КБЖУ / БЮДЖЕТА ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    builder = InlineKeyboardBuilder()
    builder.button(text="🥦 Веган", callback_data="diet_веган")
    builder.button(text="🥛 Без лактозы", callback_data="diet_непереносимость лактозы")
    builder.button(text="🚫 Аллергия на продукты", callback_data="diet_аллергия")
    builder.button(text="🩸 Диабет", callback_data="diet_диабет")
    builder.button(text="🍽️ Нет ограничений", callback_data="diet_нет")
    builder.adjust(1)
    
    await message.answer(
        "Привет! Давайте настроим ваш профиль питания.\n"
        "Выберите ваши особенности диеты:",
        reply_markup=builder.as_markup()
    )
    await state.set_state(BotStates.waiting_for_diet)

@dp.callback_query(BotStates.waiting_for_diet, F.data.startswith("diet_"))
async def process_diet(callback: types.CallbackQuery, state: FSMContext):
    diet = callback.data.split("_")[1]
    await state.update_data(diet=diet)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="Мужской ♂️", callback_data="gender_м")
    builder.button(text="Женский ♀️", callback_data="gender_ж")
    
    await callback.message.answer("Выберите ваш пол для расчета калорий:", reply_markup=builder.as_markup())
    await state.set_state(BotStates.waiting_for_gender)
    await callback.answer()

@dp.callback_query(BotStates.waiting_for_gender, F.data.startswith("gender_"))
async def process_gender(callback: types.CallbackQuery, state: FSMContext):
    gender = callback.data.split("_")[1]
    await state.update_data(gender=gender)
    await callback.message.answer("Введите ваш вес в кг (например: 75):")
    await state.set_state(BotStates.waiting_for_weight)
    await callback.answer()

@dp.message(BotStates.waiting_for_weight)
async def process_weight(message: types.Message, state: FSMContext):
    try:
        weight = float(message.text.strip())
        await state.update_data(weight=weight)
        await message.answer("Укажите ваш рост в см (например: 176):")
        await state.set_state(BotStates.waiting_for_height)
    except ValueError:
        await message.answer("Введите число (например: 75):")

@dp.message(BotStates.waiting_for_height)
async def process_height(message: types.Message, state: FSMContext):
    try:
        height = float(message.text.strip())
        await state.update_data(height=height)
        await message.answer("Укажите ваш возраст (полных лет, например: 25):")
        await state.set_state(BotStates.waiting_for_age)
    except ValueError:
        await message.answer("Введите число (например: 176):")

@dp.message(BotStates.waiting_for_age)
async def process_age(message: types.Message, state: FSMContext):
    try:
        age = int(message.text.strip())
        await state.update_data(age=age)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="Низкая (сидячая работа)", callback_data="act_1.2")
        builder.button(text="Средняя (активность 1-3 р/нед)", callback_data="act_1.375")
        builder.button(text="Высокая (спорт 4-5 р/нед)", callback_data="act_1.55")
        builder.adjust(1)
        
        await message.answer("Выберите уровень физической активности:", reply_markup=builder.as_markup())
        await state.set_state(BotStates.waiting_for_activity)
    except ValueError:
        await message.answer("Введите число (например: 25):")

@dp.callback_query(BotStates.waiting_for_activity, F.data.startswith("act_"))
async def process_activity(callback: types.CallbackQuery, state: FSMContext):
    factor = float(callback.data.split("_")[1])
    await state.update_data(activity_factor=factor)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="В день ☀️", callback_data="period_day")
    builder.button(text="В неделю 📅", callback_data="period_week")
    builder.button(text="В месяц 🗓️", callback_data="period_month")
    builder.adjust(3)
    
    await callback.message.answer("За какой период вам удобнее ограничить бюджет на еду?", reply_markup=builder.as_markup())
    await state.set_state(BotStates.waiting_for_budget_period)
    await callback.answer()

@dp.callback_query(BotStates.waiting_for_budget_period, F.data.startswith("period_"))
async def process_budget_period(callback: types.CallbackQuery, state: FSMContext):
    period = callback.data.split("_")[1]
    await state.update_data(budget_period=period)
    
    period_text = "в день" if period == "day" else "в неделю" if period == "week" else "в месяц"
    await callback.message.answer(f"Какую сумму (в рублях) вы готовы тратить на еду {period_text}?")
    await state.set_state(BotStates.waiting_for_budget_amount)
    await callback.answer()

@dp.message(BotStates.waiting_for_budget_amount)
async def process_budget_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.strip())
        await state.update_data(budget_amount=amount)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="Пятёрочка 🛒", callback_data="store_пятерочка")
        builder.button(text="Магнит 🛍️", callback_data="store_магнит")
        builder.button(text="Самокат ⚡", callback_data="store_самокат")
        builder.button(text="Лента 🏬", callback_data="store_лента")
        builder.adjust(2)
        
        await message.answer("Выберите магазин для оценки цен:", reply_markup=builder.as_markup())
        await state.set_state(BotStates.waiting_for_store)
    except ValueError:
        await message.answer("Пожалуйста, введите сумму числом:")

@dp.callback_query(BotStates.waiting_for_store, F.data.startswith("store_"))
async def process_store(callback: types.CallbackQuery, state: FSMContext):
    store = callback.data.split("_")[1]
    data = await state.get_data()
    user_id = callback.from_user.id
    
    calories, proteins, fats, carbs = calculate_kbzhu(
        gender=data["gender"], weight=data["weight"], height=data["height"], age=data["age"], activity_factor=data["activity_factor"]
    )
    
    raw_amount = data["budget_amount"]
    period = data["budget_period"]
    if period == "month":
        daily_budget = raw_amount / 30.0
    elif period == "week":
        daily_budget = raw_amount / 7.0
    else:
        daily_budget = raw_amount
        
    USER_DATA[user_id] = {
        "diet": data["diet"],
        "gender": data["gender"],
        "weight": data["weight"],
        "height": data["height"],
        "age": data["age"],
        "activity_factor": data["activity_factor"],
        "daily_calories": calories,
        "proteins": proteins,
        "fats": fats,
        "carbs": carbs,
        "budget_limit": daily_budget,
        "store": store,
        "notifications_enabled": True,  # По умолчанию включены
        "inventory": []
    }
    
    summary = (
        "📊 *Профиль питания успешно настроен!*\n\n"
        f"🏃‍♂️ *Суточная норма КБЖУ:*\n"
        f"▪️ Калории: *{calories} ккал*\n"
        f"▪️ Белки: *{proteins} г*\n"
        f