from aiogram.fsm.state import State, StatesGroup


class CalculatorStates(StatesGroup):
    waiting_contract_price = State()
    waiting_planned_transfer_date = State()
    waiting_client_type = State()
    waiting_deadline_confirmation = State()
    waiting_unique_object = State()
    waiting_ddu_signing_date = State()
    waiting_acceptance_evasion = State()
    waiting_object_transfer_status = State()
    waiting_actual_transfer_date = State()


class DocumentUploadStates(StatesGroup):
    choosing_type = State()
    waiting_file = State()


class ConsultationDescriptionStates(StatesGroup):
    waiting_subject_choice = State()
    waiting_description = State()
    reviewing_description = State()
