"""Mode routing primitives for the four-workspace application shell."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType


class ModeId(StrEnum):
    """Stable identifiers used by navigation, persistence and feature ownership."""

    VIEWER = "viewer"
    CONVERT = "convert"
    EDIT = "edit"
    GENERATE = "generate"


def _mode_id(value: ModeId | str) -> ModeId:
    try:
        return value if isinstance(value, ModeId) else ModeId(value)
    except ValueError as error:
        supported = ", ".join(mode.value for mode in ModeId)
        raise ValueError(f"unknown shell mode {value!r}; expected one of: {supported}") from error


def _required_id(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be blank")
    return normalized


def _frozen_mapping(values: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(dict(values))


@dataclass(frozen=True, slots=True)
class AssetHandoff:
    """An image-like asset passed between shell modes without loading pixel data."""

    id: str
    uri: str | None = None
    media_type: str = "image"
    display_name: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required_id(self.id, "asset id"))
        object.__setattr__(self, "media_type", _required_id(self.media_type, "asset media type"))
        object.__setattr__(self, "metadata", _frozen_mapping(self.metadata))

    @property
    def asset_id(self) -> str:
        return self.id


@dataclass(frozen=True, slots=True)
class RevisionHandoff:
    """A non-destructive revision reference shared by viewer, converter and editor."""

    id: str
    asset_id: str
    number: int | None = None
    dirty: bool = False
    project_uri: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required_id(self.id, "revision id"))
        object.__setattr__(self, "asset_id", _required_id(self.asset_id, "revision asset id"))
        if self.number is not None and self.number < 0:
            raise ValueError("revision number must not be negative")
        object.__setattr__(self, "metadata", _frozen_mapping(self.metadata))

    @property
    def revision_id(self) -> str:
        return self.id


# Short aliases make the handoff vocabulary pleasant at integration call sites.
Asset = AssetHandoff
Revision = RevisionHandoff


@dataclass(frozen=True, slots=True)
class ModeContext:
    """Immutable snapshot of one mode's UI state and current document handoff."""

    mode: ModeId
    state: Mapping[str, object] = field(default_factory=dict)
    asset: AssetHandoff | None = None
    revision: RevisionHandoff | None = None
    handoff_from: ModeId | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", _mode_id(self.mode))
        object.__setattr__(self, "state", _frozen_mapping(self.state))
        if self.handoff_from is not None:
            object.__setattr__(self, "handoff_from", _mode_id(self.handoff_from))
        if self.revision is not None:
            if self.asset is None:
                raise ValueError("revision handoff requires an asset")
            if self.revision.asset_id != self.asset.id:
                raise ValueError("revision belongs to a different asset")

    @property
    def asset_id(self) -> str | None:
        return self.asset.id if self.asset is not None else None

    @property
    def revision_id(self) -> str | None:
        return self.revision.id if self.revision is not None else None

    def with_state(
        self, changes: Mapping[str, object], *, replace_all: bool = False
    ) -> ModeContext:
        next_state = dict(changes) if replace_all else {**self.state, **changes}
        return replace(self, state=next_state)

    def with_handoff(
        self,
        *,
        asset: AssetHandoff | None,
        revision: RevisionHandoff | None,
        handoff_from: ModeId | None = None,
    ) -> ModeContext:
        return replace(
            self,
            asset=asset,
            revision=revision,
            handoff_from=handoff_from,
        )


@dataclass(frozen=True, slots=True)
class ModeTransition:
    """Event delivered to router subscribers after a committed change."""

    previous_mode: ModeId
    mode: ModeId
    previous_context: ModeContext
    context: ModeContext
    reason: str = "switch"

    @property
    def current_mode(self) -> ModeId:
        return self.mode

    @property
    def changed_mode(self) -> bool:
        return self.previous_mode != self.mode


ModeChange = ModeTransition
ModeSubscriber = Callable[[ModeTransition], object]


class _Unset:
    pass


_UNSET = _Unset()


class ModeRouter:
    """State-preserving router for the viewer, converter, editor and generator."""

    def __init__(
        self,
        initial_mode: ModeId | str = ModeId.VIEWER,
        *,
        initial_context: ModeContext | None = None,
    ) -> None:
        mode = _mode_id(initial_mode)
        self._contexts = {item: ModeContext(item) for item in ModeId}
        if initial_context is not None:
            if initial_context.mode != mode:
                raise ValueError("initial context mode does not match initial mode")
            self._contexts[mode] = initial_context
        self._active_mode = mode
        self._subscribers: dict[int, ModeSubscriber] = {}
        self._next_subscriber_id = 1

    @property
    def active_mode(self) -> ModeId:
        return self._active_mode

    @property
    def context(self) -> ModeContext:
        return self._contexts[self._active_mode]

    @property
    def active_context(self) -> ModeContext:
        return self.context

    @property
    def modes(self) -> tuple[ModeId, ...]:
        return tuple(ModeId)

    @property
    def contexts(self) -> Mapping[ModeId, ModeContext]:
        return MappingProxyType(dict(self._contexts))

    def context_for(self, mode: ModeId | str) -> ModeContext:
        return self._contexts[_mode_id(mode)]

    def state_for(self, mode: ModeId | str) -> Mapping[str, object]:
        return self.context_for(mode).state

    def subscribe(
        self,
        subscriber: ModeSubscriber,
        *,
        emit_current: bool = False,
    ) -> Callable[[], None]:
        """Subscribe and return an idempotent unsubscription callback."""

        subscriber_id = self._next_subscriber_id
        self._next_subscriber_id += 1
        self._subscribers[subscriber_id] = subscriber
        if emit_current:
            current = self.context
            subscriber(
                ModeTransition(
                    previous_mode=self.active_mode,
                    mode=self.active_mode,
                    previous_context=current,
                    context=current,
                    reason="snapshot",
                )
            )

        def unsubscribe() -> None:
            self._subscribers.pop(subscriber_id, None)

        return unsubscribe

    def unsubscribe(self, subscriber: ModeSubscriber) -> None:
        for subscriber_id, candidate in tuple(self._subscribers.items()):
            if candidate is subscriber:
                self._subscribers.pop(subscriber_id, None)

    def switch(
        self,
        mode: ModeId | str,
        *,
        context: ModeContext | None = None,
        carry_handoff: bool = True,
        asset: AssetHandoff | None | _Unset = _UNSET,
        revision: RevisionHandoff | None | _Unset = _UNSET,
    ) -> ModeTransition:
        """Activate a mode while retaining its state and carrying the current revision."""

        target_mode = _mode_id(mode)
        previous_mode = self._active_mode
        previous_context = self.context
        if context is not None and context.mode != target_mode:
            raise ValueError("context mode does not match route target")
        target = context or self._contexts[target_mode]

        next_asset = target.asset
        next_revision = target.revision
        handoff_from = target.handoff_from
        if context is None and carry_handoff:
            next_asset = previous_context.asset
            next_revision = previous_context.revision
            handoff_from = previous_mode if previous_mode != target_mode else target.handoff_from
        if not isinstance(asset, _Unset):
            next_asset = asset
            handoff_from = previous_mode if previous_mode != target_mode else target.handoff_from
            if isinstance(revision, _Unset) and next_revision is not None:
                if next_asset is None or next_revision.asset_id != next_asset.id:
                    next_revision = None
        if not isinstance(revision, _Unset):
            next_revision = revision
            handoff_from = previous_mode if previous_mode != target_mode else target.handoff_from

        target = target.with_handoff(
            asset=next_asset,
            revision=next_revision,
            handoff_from=handoff_from,
        )
        self._contexts[target_mode] = target
        self._active_mode = target_mode
        transition = ModeTransition(
            previous_mode=previous_mode,
            mode=target_mode,
            previous_context=previous_context,
            context=target,
            reason="switch",
        )
        if previous_mode != target_mode or previous_context != target:
            self._notify(transition)
        return transition

    def activate(
        self,
        mode: ModeId | str,
        *,
        context: ModeContext | None = None,
        carry_handoff: bool = True,
        asset: AssetHandoff | None | _Unset = _UNSET,
        revision: RevisionHandoff | None | _Unset = _UNSET,
    ) -> ModeTransition:
        """Alias for integrations that use the term ``activate`` for navigation."""

        return self.switch(
            mode,
            context=context,
            carry_handoff=carry_handoff,
            asset=asset,
            revision=revision,
        )

    def update_state(
        self,
        changes: Mapping[str, object] | None = None,
        *,
        mode: ModeId | str | None = None,
        **values: object,
    ) -> ModeContext:
        patch = dict(changes or {})
        patch.update(values)
        target_mode = self._active_mode if mode is None else _mode_id(mode)
        previous = self._contexts[target_mode]
        current = previous.with_state(patch)
        self._contexts[target_mode] = current
        self._notify_context_change(target_mode, previous, current, "state")
        return current

    def replace_state(
        self,
        state: Mapping[str, object],
        *,
        mode: ModeId | str | None = None,
    ) -> ModeContext:
        target_mode = self._active_mode if mode is None else _mode_id(mode)
        previous = self._contexts[target_mode]
        current = previous.with_state(state, replace_all=True)
        self._contexts[target_mode] = current
        self._notify_context_change(target_mode, previous, current, "state")
        return current

    def set_handoff(
        self,
        asset: AssetHandoff | None,
        revision: RevisionHandoff | None = None,
        *,
        mode: ModeId | str | None = None,
    ) -> ModeContext:
        target_mode = self._active_mode if mode is None else _mode_id(mode)
        previous = self._contexts[target_mode]
        current = previous.with_handoff(
            asset=asset,
            revision=revision,
            handoff_from=previous.handoff_from,
        )
        self._contexts[target_mode] = current
        self._notify_context_change(target_mode, previous, current, "handoff")
        return current

    def _notify_context_change(
        self,
        mode: ModeId,
        previous: ModeContext,
        current: ModeContext,
        reason: str,
    ) -> None:
        if previous == current or mode != self._active_mode:
            return
        self._notify(
            ModeTransition(
                previous_mode=mode,
                mode=mode,
                previous_context=previous,
                context=current,
                reason=reason,
            )
        )

    def _notify(self, transition: ModeTransition) -> None:
        for subscriber in tuple(self._subscribers.values()):
            subscriber(transition)
