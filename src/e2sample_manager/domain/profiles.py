"""Device slot and sample-memory profiles."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    name: str
    slot_ranges: tuple[range, ...]
    memory_limit: int = 26_214_396
    user_slot_start: int = 501

    def accepts_slot(self, slot: int) -> bool:
        return any(slot in slots for slots in self.slot_ranges)

    def is_user_slot(self, slot: int) -> bool:
        return self.accepts_slot(slot) and slot >= self.user_slot_start

    def slots_from(self, first: int) -> list[int]:
        return [slot for slots in self.slot_ranges for slot in slots if slot >= first]


# Factory replacement slots 19..421 and user slots 501..999 are the ranges
# exposed by the stock Electribe Sampler.
KORG_PROFILE = DeviceProfile(
    name="KORG electribe sampler",
    slot_ranges=(range(19, 422), range(501, 1000)),
)

# Hacktribe reserves 1..500 for oscillators. The pointer table has 1020 entries,
# but this does not make every entry a usable device sample slot.
HACKTRIBE_PROFILE = DeviceProfile(
    name="Hacktribe",
    slot_ranges=(range(501, 1000),),
    user_slot_start=501,
)

PROFILES = {
    "korg": KORG_PROFILE,
    "hacktribe": HACKTRIBE_PROFILE,
}
