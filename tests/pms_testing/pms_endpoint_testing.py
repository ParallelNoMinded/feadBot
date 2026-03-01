import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from uuid import UUID
import httpx
import structlog
from sqlalchemy import select

from app.core.db import AsyncSessionFactory
from shared_models import Reservation as PMSReservation, User, UserHotel, ReservationUsers

logger = structlog.get_logger(__name__)

SCENARIOS_FILE = Path("test_pms_data_scenarios.json")
ENDPOINT_URL = "http://localhost:8000/api/reservations"

TEST_USERS = {
    "1": {
        "phone": "79622011788",
        "telegram_id": "1959291428",
        "name": "Иван Петров",
    },
    "2": {
        "phone": "79536608182",
        "telegram_id": "7175803578",
        "name": "Мария Сидорова",
    },
    "3": {
        "phone": "79123456789",
        "telegram_id": "1234567890",
        "name": "Алексей Козлов",
    },
    "4": {
        "phone": "79234567890",
        "telegram_id": "2345678901",
        "name": "Елена Новикова",
    },
    "5": {
        "phone": "79345678901",
        "telegram_id": "3456789012",
        "name": "Дмитрий Волков",
    },
}

ROOMS = ["101", "202", "303", "404", "505"]


def format_date(dt: datetime) -> str:
    return dt.strftime("%d.%m.%Y %H:%M:%S")


def create_reservation(ref: str, phone: str, arrival: datetime, departure: datetime, status: str, room_no: str) -> dict:
    name_parts = TEST_USERS.get(next((k for k, v in TEST_USERS.items() if v["phone"] == phone), "1"))["name"].split()

    return {
        "SortCode": f"00060000000{room_no}00000060",
        "ConfNumber": f"M{ref[:8].replace('-', '')}",
        "ReservationStatus": status,
        "ArrivalDate": format_date(arrival),
        "DepartureDate": format_date(departure),
        "Title": "",
        "FirstName": name_parts[0],
        "LastName": name_parts[1] if len(name_parts) > 1 else "",
        "MiddleName": "",
        "PhoneNumber": phone,
        "Email": f"test{phone}@example.com",
        "Adults": 1,
        "Children": 0,
        "VipCode": "",
        "GroupNameID": "",
        "CompanyName": "",
        "RoomType": "Standard 1-местный",
        "RoomNo": room_no,
        "CreationDate": format_date(arrival),
        "Comments": "Тестовая резервация",
        "TotalAmount": 25000,
        "CurrencyCode": "Руб.",
        "Hotel": "Alean",
        "RateAmount": 5000,
        "AccommodationTemplate": "1(1м)",
        "RoomRate": "Стандарт",
        "Ref": ref,
        "Guest": [
            {
                "FirstName": name_parts[0],
                "LastName": name_parts[1] if len(name_parts) > 1 else "",
                "MiddleName": "",
                "PhoneNumber": phone,
                "Email": f"test{phone}@example.com",
                "Ref": ref,
            }
        ],
    }


def load_scenarios() -> dict:
    if not SCENARIOS_FILE.exists():
        return {"reservations": []}
    with open(SCENARIOS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_scenarios(data: dict) -> None:
    with open(SCENARIOS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def find_user_reservation(reservations: list, phone: str) -> tuple[int, dict] | tuple[None, None]:
    for idx, reservation in enumerate(reservations):
        if reservation.get("PhoneNumber") == phone:
            return idx, reservation
    return None, None


async def send_to_endpoint(reservations: list[dict]) -> dict:
    payload = {"reservations": reservations}

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            ENDPOINT_URL,
            headers={"Token": "test-token", "Content-Type": "application/json"},
            json=payload,
        )
        response.raise_for_status()
        return response.json()


async def check_in_db(reservation_ref: str) -> PMSReservation:
    async with AsyncSessionFactory() as session:

        reservation = await session.get(PMSReservation, UUID(reservation_ref))
        if not reservation:
            raise ValueError(f"Reservation {reservation_ref} not found in the database")
        return reservation


async def check_user_hotel(telegram_id: str, expected_ref: str) -> UserHotel | None:
    async with AsyncSessionFactory() as session:
        user_result = await session.execute(select(User).where(User.external_user_id == telegram_id))
        user = user_result.scalars().first()
        if not user:
            return None

        user_hotel_result = await session.execute(
            select(UserHotel)
            .where(UserHotel.user_id == user.id, UserHotel.close.is_(None))
            .order_by(UserHotel.open.desc())
            .limit(1)
        )
        user_hotel = user_hotel_result.scalars().first()
        return user_hotel


async def check_user_reservation_link(telegram_id: str, reservation_ref: str) -> None:
    async with AsyncSessionFactory() as session:

        user_result = await session.execute(select(User).where(User.external_user_id == telegram_id))
        user = user_result.scalars().first()
        if not user:
            logger.info("INFO: The user was not found for checking the reservations_users connection")
            return

        reservation_id = UUID(reservation_ref)
        link_result = await session.execute(
            select(ReservationUsers).where(
                ReservationUsers.user_id == user.id,
                ReservationUsers.reservation_id == reservation_id,
            )
        )
        link = link_result.scalars().first()

        if link:
            logger.info(
                f"OK: The link in reservations_users has been created: user_id={user.id}, reservation_id={reservation_id}"
            )
        else:
            logger.info(
                "INFO: No connection was found in reservations_users (the user may not be registered or the phone number does not match)"
            )


def show_users():
    logger.info("\nAvailable users:")
    for key, user_info in TEST_USERS.items():
        logger.info(f"  {key}. {user_info['name']} ({user_info['phone']})")


def get_user_choice() -> dict:
    show_users()
    while True:
        choice = input("\nSelect the user (number): ").strip()
        if choice in TEST_USERS:
            return TEST_USERS[choice]
        print("Wrong choice. Try again.")


def get_scenario_choice() -> str:
    scenarios = {
        "1": ("CHECKED_OUT", "CHECKED_OUT"),
        "2": ("Extended stay", "EXTEND"),
        "3": ("Changed the room number", "ROOM_CHANGE"),
        "4": ("New settlement", "NEW_CHECKIN"),
        "5": ("Checked in for N days", "CHECKIN_N_DAYS"),
    }

    logger.info("\nUpgrade Scenarios:")
    for key, (name, _) in scenarios.items():
        logger.info(f"  {key}. {name}")

    while True:
        choice = input("\nSelect the script (number): ").strip()
        if choice in scenarios:
            return scenarios[choice][1]
        print("Wrong choice. Try again.")


def update_checkout(reservation: dict, now: datetime) -> dict:
    reservation["ReservationStatus"] = "CHECKED_OUT"
    reservation["DepartureDate"] = format_date(now - timedelta(days=2))
    return reservation


def update_extend(reservation: dict) -> dict:
    original_departure = datetime.strptime(reservation["DepartureDate"], "%d.%m.%Y %H:%M:%S")
    original_departure = original_departure.replace(tzinfo=timezone.utc)
    new_departure = original_departure + timedelta(days=2)
    reservation["DepartureDate"] = format_date(new_departure)
    return reservation


def update_room_change(reservation: dict) -> dict:
    current_room = reservation.get("RoomNo", "101")
    available_rooms = [r for r in ROOMS if r != current_room]
    if not available_rooms:
        available_rooms = ROOMS

    logger.info(f"\nCurrent number: {current_room}")
    logger.info(f"Available rooms: {', '.join(available_rooms)}")

    while True:
        new_room = input("Enter a new number: ").strip()
        if new_room in ROOMS and new_room != current_room:
            reservation["RoomNo"] = new_room
            return reservation
        print("Incorrect number. Try again.")


def update_new_checkin(reservation: dict, now: datetime) -> dict:
    reservation["Ref"] = str(uuid4())
    reservation["ReservationStatus"] = "IN_HOUSE"
    reservation["ArrivalDate"] = format_date(now)
    reservation["DepartureDate"] = format_date(now + timedelta(days=3))
    reservation["RoomNo"] = input(f"Room number: ({', '.join(ROOMS)}): ").strip() or ROOMS[0]
    return reservation


def update_checkin_n_days(reservation: dict, now: datetime) -> dict:
    while True:
        try:
            days = int(input("Number of days (1-30): ").strip())
            if 1 <= days <= 30:
                reservation["Ref"] = str(uuid4())
                reservation["ReservationStatus"] = "IN_HOUSE"
                reservation["ArrivalDate"] = format_date(now)
                reservation["DepartureDate"] = format_date(now + timedelta(days=days))
                reservation["RoomNo"] = input(f"Room number ({', '.join(ROOMS)}): ").strip() or ROOMS[0]
                return reservation
            print("The number of days should be from 1 to 30.")
        except ValueError:
            print("Enter a number.")


def close_user_reservations(reservations: list, phone: str, now: datetime):
    for res in reservations:
        if res.get("PhoneNumber") == phone and res.get("ReservationStatus") == "IN_HOUSE":
            res["ReservationStatus"] = "CHECKED_OUT"
            res["DepartureDate"] = format_date(now - timedelta(days=1))


async def process_scenario(user: dict, scenario: str):
    now = datetime.now(timezone.utc)
    data = load_scenarios()
    reservations = data.get("reservations", [])

    idx, existing = find_user_reservation(reservations, user["phone"])

    if existing:
        reservation = existing.copy()
        logger.info("\nCurrent reservation:")
        logger.info(f"  Ref: {reservation['Ref']}")
        logger.info(f"  Status: {reservation['ReservationStatus']}")
        logger.info(f"  Room: {reservation['RoomNo']}")
        logger.info(f"  Arrival: {reservation['ArrivalDate']}")
        logger.info(f"  Checked out: {reservation['DepartureDate']}")
    else:
        reservation = create_reservation(
            str(uuid4()),
            user["phone"],
            now - timedelta(days=1),
            now + timedelta(days=2),
            "IN_HOUSE",
            ROOMS[0],
        )

    scenario_handlers = {
        "CHECKED_OUT": lambda r: update_checkout(r, now),
        "EXTEND": update_extend,
        "ROOM_CHANGE": update_room_change,
        "NEW_CHECKIN": lambda r: update_new_checkin(r, now),
        "CHECKIN_N_DAYS": lambda r: update_checkin_n_days(r, now),
    }

    updated_reservation = scenario_handlers[scenario](reservation)

    reservations_to_send = [updated_reservation]

    if scenario in ["NEW_CHECKIN", "CHECKIN_N_DAYS"]:
        closed_reservations = []
        for res in reservations:
            if (
                res.get("PhoneNumber") == user["phone"]
                and res.get("ReservationStatus") == "IN_HOUSE"
                and res.get("Ref") != updated_reservation["Ref"]
            ):
                closed_res = res.copy()
                closed_res["ReservationStatus"] = "CHECKED_OUT"
                closed_res["DepartureDate"] = format_date(now - timedelta(days=1))
                closed_reservations.append(closed_res)
        if closed_reservations:
            reservations_to_send.extend(closed_reservations)
        close_user_reservations(reservations, user["phone"], now)

    if idx is not None:
        reservations[idx] = updated_reservation
    else:
        reservations.append(updated_reservation)

    for res in reservations:
        if res.get("PhoneNumber") == user["phone"] and res.get("ReservationStatus") == "CHECKED_OUT":
            for closed in reservations_to_send[1:]:
                if closed.get("Ref") == res.get("Ref"):
                    res.update(closed)

    data["reservations"] = reservations
    save_scenarios(data)

    logger.info(f"\nOK: The data is saved in {SCENARIOS_FILE}")

    await send_to_endpoint(reservations_to_send)
    logger.info(f"OK: Sent to the endpoint: {len(reservations_to_send)} reservations")

    await asyncio.sleep(0.5)

    db_reservation = await check_in_db(updated_reservation["Ref"])
    logger.info(f"OK: Checked in the database: reservation {db_reservation.id}")

    user_hotel = await check_user_hotel(user["telegram_id"], updated_reservation["Ref"])
    if user_hotel:
        if user_hotel.external_pms_id == updated_reservation["Ref"]:
            logger.info(f"OK: external_pms_id is set: {user_hotel.external_pms_id}")
        else:
            logger.info(
                f"INFO: external_pms_id = {user_hotel.external_pms_id} (may indicate another active reservation)"
            )
            logger.info(f"INFO: The Ref of the new reservation was expected: {updated_reservation['Ref']}")
    else:
        logger.info("INFO: UserHotel not found (user may not be registered)")

    await check_user_reservation_link(user["telegram_id"], updated_reservation["Ref"])


async def main():
    logger.info("Testing PMS integration")

    user = get_user_choice()
    scenario = get_scenario_choice()

    logger.info(f"\nUser: {user['name']} {user['phone']}")
    logger.info(f"Scenario: {scenario}")

    await process_scenario(user, scenario)

    logger.info("\n" + "=" * 60)
    logger.info("Done!")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
