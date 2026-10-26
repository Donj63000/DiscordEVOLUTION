"""Checked internal command calls with typed, non-reparsed arguments.

Context.invoke deliberately bypasses discord.py's command checks. AI tools must
use this adapter instead: it preserves global/group checks, conversions,
cooldowns, concurrency limits and before/after hooks.
"""
from __future__ import annotations

import inspect
from typing import get_args

import discord
from discord.ext import commands
from discord.ext.commands.converter import run_converters


async def check_command(ctx, command: commands.Command) -> None:
    if not isinstance(command, commands.Command):
        raise commands.CheckFailure("Commande interne invalide.")
    if not await ctx.bot.can_run(ctx, call_once=True):
        raise commands.CheckFailure("Le contrôle global a refusé cette commande.")
    for item in [*reversed(command.parents), command]:
        if not await item.can_run(ctx):
            raise commands.CheckFailure("Les permissions de la commande ont changé.")


async def convert_arguments(ctx, command: commands.Command, args, kwargs):
    """Bind against the actual callback contract, then run SDK converters."""
    parameters = command.clean_params
    try:
        bound = inspect.Signature(parameters.values()).bind(*args, **kwargs)
    except TypeError as exc:
        raise commands.BadArgument("Arguments incompatibles avec la commande.") from exc

    async def convert(parameter, value):
        converter = parameter.converter
        if value is None and (parameter.default is None or type(None) in get_args(converter)):
            return None
        if converter is inspect.Parameter.empty:
            return value
        # Values already resolved by a dedicated tool must retain their identity.
        if isinstance(converter, type) and isinstance(value, converter):
            return value
        if not isinstance(value, str):
            value = str(value)
        return await run_converters(ctx, converter, value, parameter)

    for name, value in list(bound.arguments.items()):
        parameter = parameters[name]
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            bound.arguments[name] = tuple([await convert(parameter, item) for item in value])
        elif parameter.kind is inspect.Parameter.VAR_KEYWORD:
            bound.arguments[name] = {key: await convert(parameter, item) for key, item in value.items()}
        else:
            bound.arguments[name] = await convert(parameter, value)
    return bound.args, bound.kwargs


def freeze_argument(value):
    """Store Discord identities as IDs, never as mutable display names."""
    if isinstance(value, (discord.Member, discord.User, discord.Role, discord.abc.GuildChannel)):
        return str(value.id)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise commands.BadArgument("Cet argument ne peut pas être confirmé par l'IA.")


async def invoke_checked(ctx, command: commands.Command, *args, **kwargs):
    await check_command(ctx, command)
    args, kwargs = await convert_arguments(ctx, command, args, kwargs)
    previous_command = ctx.command
    ctx.args = ([command.cog] if command.cog is not None else []) + [ctx, *args]
    ctx.kwargs = kwargs
    ctx.invoked_with = command.name
    ctx.invoked_parents = [parent.name for parent in reversed(command.parents)]
    prepared = []
    acquired = []
    try:
        for item in [*reversed(command.parents), command]:
            ctx.command = item
            if item._max_concurrency is not None:
                await item._max_concurrency.acquire(ctx.message)
                acquired.append(item)
            item._prepare_cooldowns(ctx)
            await item.call_before_hooks(ctx)
            prepared.append(item)
        ctx.command = command
        ctx.invoked_subcommand = None
        ctx.subcommand_passed = None
        if command.cog is not None:
            return await command.callback(command.cog, ctx, *args, **kwargs)
        return await command.callback(ctx, *args, **kwargs)
    except commands.CommandError:
        ctx.command_failed = True
        raise
    except Exception as exc:
        ctx.command_failed = True
        raise commands.CommandInvokeError(exc) from exc
    finally:
        try:
            for item in reversed(prepared):
                ctx.command = item
                await item.call_after_hooks(ctx)
        finally:
            for item in reversed(acquired):
                await item._max_concurrency.release(ctx.message)
            ctx.command = previous_command
