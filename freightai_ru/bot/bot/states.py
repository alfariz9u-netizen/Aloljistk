from aiogram.fsm.state import State, StatesGroup


class Onboarding(StatesGroup):
    name = State()
    phone = State()
    awaiting_request = State()  # waiting for the free-text description


class ManualEntry(StatesGroup):
    """Fallback stepwise entry, used when AI extraction fails/is
    unavailable, or when the user chooses manual entry directly. FSM data
    stores 'entry_type' = 'truck' | 'load' to know which fields to ask."""
    current_city = State()          # truck: current location / load: origin
    destination = State()           # truck: desired destination / load: destination
    truck_type = State()
    truck_count = State()           # load only
    has_trip = State()              # truck only
    trip_destination = State()      # truck only
    trip_eta = State()              # truck only


class FreeTextConfirm(StatesGroup):
    reviewing = State()
