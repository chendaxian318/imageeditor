"""Provider-neutral generation service discovery and pricing metadata."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from light_image_editor.providers import ProviderCapability, ProviderManifest


class ProviderAvailability(StrEnum):
    READY = "ready"
    NEEDS_CONFIGURATION = "needs_configuration"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class GenerationProviderProfile:
    manifest: ProviderManifest
    model_id: str
    display_name: str
    availability: ProviderAvailability = ProviderAvailability.READY
    status_detail: str = "可用"
    estimated_cost_minor_per_image: int = 0
    currency_code: str = "CNY"

    def __post_init__(self) -> None:
        if not self.display_name.strip() or not self.status_detail.strip():
            raise ValueError("服务名称和状态说明不能为空")
        if not self.manifest.supports(ProviderCapability.GENERATION, self.model_id):
            raise ValueError("服务配置必须声明所选模型的生图能力")
        if (
            isinstance(self.estimated_cost_minor_per_image, bool)
            or not isinstance(self.estimated_cost_minor_per_image, int)
            or self.estimated_cost_minor_per_image < 0
        ):
            raise ValueError("单张预估费用必须使用非负整数最小货币单位")
        if (
            len(self.currency_code) != 3
            or not self.currency_code.isascii()
            or not self.currency_code.isalpha()
        ):
            raise ValueError("货币代码必须是三个 ASCII 字符")
        object.__setattr__(self, "currency_code", self.currency_code.upper())

    @property
    def provider_id(self) -> str:
        return self.manifest.provider_id

    @property
    def selectable(self) -> bool:
        return self.availability is ProviderAvailability.READY

    def estimated_cost_minor(self, count: int) -> int:
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError("候选数量必须是正整数")
        return self.estimated_cost_minor_per_image * count


class GenerationProviderCatalog:
    def __init__(self, profiles: tuple[GenerationProviderProfile, ...]) -> None:
        if not profiles:
            raise ValueError("生图服务目录不能为空")
        identities = [(item.provider_id, item.model_id) for item in profiles]
        labels = [item.display_name for item in profiles]
        if len(set(identities)) != len(identities):
            raise ValueError("生图服务与模型组合不能重复")
        if len(set(labels)) != len(labels):
            raise ValueError("生图服务显示名称不能重复")
        self._profiles = profiles
        self._by_label = {item.display_name: item for item in profiles}

    @property
    def profiles(self) -> tuple[GenerationProviderProfile, ...]:
        return self._profiles

    def by_display_name(self, display_name: str) -> GenerationProviderProfile:
        try:
            return self._by_label[display_name]
        except KeyError as error:
            raise KeyError(f"未知生图服务：{display_name}") from error

    def default(self) -> GenerationProviderProfile:
        for profile in self._profiles:
            if profile.selectable:
                return profile
        return self._profiles[0]
