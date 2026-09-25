from aiogram.fsm.state import State, StatesGroup


class CalculatorStates(StatesGroup):
    waiting_contract_price = State()
    waiting_planned_transfer_date = State()
    waiting_object_transfer_status = State()
    waiting_actual_transfer_date = State()
    waiting_client_type = State()
    waiting_unique_object = State()


class PreviewCalculatorStates(StatesGroup):
    """Ephemeral calculator state.

    Facts in this group deliberately live only in FSM storage. They do not own a
    Case, CalculationIntake or Calculation until the client explicitly saves the
    completed preview.
    """

    waiting_contract_price = State()
    waiting_planned_transfer_date = State()
    waiting_object_transfer_status = State()
    waiting_actual_transfer_date = State()
    waiting_client_type = State()
    waiting_unique_object = State()
    result_ready = State()


class DocumentUploadStates(StatesGroup):
    choosing_type = State()
    waiting_file = State()


class ConsultationDescriptionStates(StatesGroup):
    waiting_subject_choice = State()
    waiting_description = State()
    reviewing_description = State()
