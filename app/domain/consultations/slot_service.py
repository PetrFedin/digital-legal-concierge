from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
@dataclass
class ConsultationSlot:
    code:str; title:str; starts_at:datetime; ends_at:datetime
class SlotService:
    def get_available_slots(self):
        now=datetime.now(timezone.utc); starts=[now+timedelta(days=1,hours=10),now+timedelta(days=1,hours=15),now+timedelta(days=2,hours=11),now+timedelta(days=2,hours=17)]
        return [ConsultationSlot(f'slot_{i}',s.strftime('%d.%m.%Y %H:%M'),s,s+timedelta(minutes=60)) for i,s in enumerate(starts,1)]
    def find_slot(self,code):
        return next((s for s in self.get_available_slots() if s.code==code),None)
