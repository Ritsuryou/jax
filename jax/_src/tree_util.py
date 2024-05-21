# Copyright 2018 The JAX Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from __future__ import annotations

import collections
from collections.abc import Hashable, Iterable
from dataclasses import dataclass
import difflib
import functools
from functools import partial
import operator as op
import textwrap
from typing import Any, Callable, NamedTuple, Sequence, TypeVar, Union, overload

from jax._src import traceback_util
from jax._src.lib import pytree
from jax._src.util import safe_zip, set_module
from jax._src.util import unzip2


export = set_module('jax.tree_util')

traceback_util.register_exclusion(__file__)

T = TypeVar("T")
Typ = TypeVar("Typ", bound=type[Any])
H = TypeVar("H", bound=Hashable)

Leaf = Any
PyTreeDef = pytree.PyTreeDef

default_registry = pytree.default_registry()
# Set __module__ and __name__, which allow this registry to be pickled by
# reference.
default_registry.__module__ = __name__
default_registry.__name__ = "default_registry"

# A copy of the default registry, where None is a leaf.
none_leaf_registry = pytree.PyTreeRegistry(
    enable_none=False, enable_tuple=True, enable_namedtuple=True,
    enable_list=True, enable_dict=True)
none_leaf_registry.__module__ = __name__
none_leaf_registry.__name__ = "none_leaf_registry"

# A special, internal pytree registry that includes everything in
# `default_registry`, plus internal Python-defined types that we want
# to teach the fast dispatch path ("C++ dispatch") how to flatten and
# unflatten. A key example is PRNG key arrays, which are currently a
# Python-defined class (in `jax._src.prng`). These ought to be a leaf
# node everywhere in the system (e.g. in Jaxpr), but we want to unpack
# and repack them across the fast dispatch boundary. If we were to
# skip registering such types here, the fast dispatch path would not
# know how to handle them as arguments. It would instead always
# indicate a "cache miss" and dispatch on the slow path.
dispatch_registry = pytree.PyTreeRegistry(
    enable_none=True, enable_tuple=True, enable_namedtuple=True,
    enable_list=True, enable_dict=True)
dispatch_registry.__module__ = __name__
dispatch_registry.__name__ = "dispatch_registry"


@export
def tree_flatten(tree: Any,
                 is_leaf: Callable[[Any], bool] | None = None
                 ) -> tuple[list[Leaf], PyTreeDef]:
  """Flattens a pytree.

  The flattening order (i.e. the order of elements in the output list)
  is deterministic, corresponding to a left-to-right depth-first tree
  traversal.

  Args:
    tree: a pytree to flatten.
    is_leaf: an optionally specified function that will be called at each
      flattening step. It should return a boolean, with true stopping the
      traversal and the whole subtree being treated as a leaf, and false
      indicating the flattening should traverse the current object.

  Returns:
    A pair where the first element is a list of leaf values and the second
    element is a treedef representing the structure of the flattened tree.

  Example:
    >>> import jax
    >>> vals, treedef = jax.tree.flatten([1, (2, 3), [4, 5]])
    >>> vals
    [1, 2, 3, 4, 5]
    >>> treedef
    PyTreeDef([*, (*, *), [*, *]])

  See Also:
    - :func:`jax.tree.leaves`
    - :func:`jax.tree.structure`
    - :func:`jax.tree.unflatten`
  """
  return default_registry.flatten(tree, is_leaf)


@export
def tree_unflatten(treedef: PyTreeDef, leaves: Iterable[Leaf]) -> Any:
  """Reconstructs a pytree from the treedef and the leaves.

  The inverse of :func:`tree_flatten`.

  Args:
    treedef: the treedef to reconstruct
    leaves: the iterable of leaves to use for reconstruction. The iterable must
      match the leaves of the treedef.

  Returns:
    The reconstructed pytree, containing the ``leaves`` placed in the structure
    described by ``treedef``.

  Example:
    >>> import jax
    >>> vals, treedef = jax.tree.flatten([1, (2, 3), [4, 5]])
    >>> newvals = [100, 200, 300, 400, 500]
    >>> jax.tree.unflatten(treedef, newvals)
    [100, (200, 300), [400, 500]]

  See Also:
    - :func:`jax.tree.flatten`
    - :func:`jax.tree.leaves`
    - :func:`jax.tree.structure`
  """
  return treedef.unflatten(leaves)


@export
def tree_leaves(tree: Any,
                is_leaf: Callable[[Any], bool] | None = None
                ) -> list[Leaf]:
  """Gets the leaves of a pytree.

  Args:
    tree: the pytree for which to get the leaves
    is_leaf : an optionally specified function that will be called at each
      flattening step. It should return a boolean, which indicates whether the
      flattening should traverse the current object, or if it should be stopped
      immediately, with the whole subtree being treated as a leaf.

  Returns:
    leaves: a list of tree leaves.

  Example:
    >>> import jax
    >>> jax.tree.leaves([1, (2, 3), [4, 5]])
    [1, 2, 3, 4, 5]

  See Also:
    - :func:`jax.tree.flatten`
    - :func:`jax.tree.structure`
    - :func:`jax.tree.unflatten`
  """
  return default_registry.flatten(tree, is_leaf)[0]


@export
def tree_structure(tree: Any,
                   is_leaf: None | (Callable[[Any],
                                              bool]) = None) -> PyTreeDef:
  """Gets the treedef for a pytree.

  Args:
    tree: the pytree for which to get the leaves
    is_leaf : an optionally specified function that will be called at each
      flattening step. It should return a boolean, which indicates whether the
      flattening should traverse the current object, or if it should be stopped
      immediately, with the whole subtree being treated as a leaf.

  Returns:
    pytreedef: a PyTreeDef representing the structure of the tree.

  Example:
    >>> import jax
    >>> jax.tree.structure([1, (2, 3), [4, 5]])
    PyTreeDef([*, (*, *), [*, *]])

  See Also:
    - :func:`jax.tree.flatten`
    - :func:`jax.tree.leaves`
    - :func:`jax.tree.unflatten`
  """
  return default_registry.flatten(tree, is_leaf)[1]


@export
def treedef_tuple(treedefs: Iterable[PyTreeDef]) -> PyTreeDef:
  """Makes a tuple treedef from an iterable of child treedefs.

  Args:
    treedefs: iterable of PyTree structures

  Returns:
    a single treedef representing a tuple of the structures

  Example:
    >>> import jax
    >>> x = [1, 2, 3]
    >>> y = {'a': 4, 'b': 5}
    >>> x_tree = jax.tree.structure(x)
    >>> y_tree = jax.tree.structure(y)
    >>> xy_tree = jax.tree_util.treedef_tuple([x_tree, y_tree])
    >>> xy_tree == jax.tree.structure((x, y))
    True

  See Also:
    - :func:`jax.tree_util.treedef_children`
  """
  return pytree.tuple(default_registry, list(treedefs))  # type: ignore


@export
def treedef_children(treedef: PyTreeDef) -> list[PyTreeDef]:
  """Return a list of treedefs for immediate children

  Args:
    treedef: a single PyTreeDef

  Returns:
    a list of PyTreeDefs representing the children of treedef.

  Examples:
    >>> import jax
    >>> x = [(1, 2), 3, {'a': 4}]
    >>> treedef = jax.tree.structure(x)
    >>> jax.tree_util.treedef_children(treedef)
    [PyTreeDef((*, *)), PyTreeDef(*), PyTreeDef({'a': *})]
    >>> _ == [jax.tree.structure(vals) for vals in x]
    True

  See Also:
    - :func:`jax.tree_util.treedef_tuple`
  """
  return treedef.children()


@export
def treedef_is_leaf(treedef: PyTreeDef) -> bool:
  """Return True if the treedef represents a leaf.

  Args:
    treedef: tree to check

  Returns:
    True if treedef is a leaf (i.e. has a single node); False otherwise.

  Example:
    >>> import jax
    >>> tree1 = jax.tree.structure(1)
    >>> jax.tree_util.treedef_is_leaf(tree1)
    True
    >>> tree2 = jax.tree.structure([1, 2])
    >>> jax.tree_util.treedef_is_leaf(tree2)
    False
  """
  return treedef.num_nodes == 1


# treedef_is_strict_leaf is not exported.
def treedef_is_strict_leaf(treedef: PyTreeDef) -> bool:
  return treedef.num_nodes == 1 and treedef.num_leaves == 1


@export
def all_leaves(iterable: Iterable[Any],
               is_leaf: Callable[[Any], bool] | None = None) -> bool:
  """Tests whether all elements in the given iterable are all leaves.

  This function is useful in advanced cases, for example if a library allows
  arbitrary map operations on a flat iterable of leaves it may want to check
  if the result is still a flat iterable of leaves.

  Args:
    iterable: Iterable of leaves.

  Returns:
    A boolean indicating if all elements in the input are leaves.

  Example:
    >>> import jax
    >>> tree = {"a": [1, 2, 3]}
    >>> assert all_leaves(jax.tree_util.tree_leaves(tree))
    >>> assert not all_leaves([tree])
  """
  if is_leaf is None:
    return pytree.all_leaves(default_registry, iterable)
  else:
    lst = list(iterable)
    return lst == tree_leaves(lst, is_leaf)


_Children = TypeVar("_Children", bound=Iterable[Any])
_AuxData = TypeVar("_AuxData", bound=Hashable)


@export
def register_pytree_node(nodetype: type[T],
                         flatten_func: Callable[[T], tuple[_Children, _AuxData]],
                         unflatten_func: Callable[[_AuxData, _Children], T]) -> None:
  """Extends the set of types that are considered internal nodes in pytrees.

  See :ref:`example usage <pytrees>`.

  Args:
    nodetype: a Python type to register as a pytree.
    flatten_func: a function to be used during flattening, taking a value of
      type ``nodetype`` and returning a pair, with (1) an iterable for the
      children to be flattened recursively, and (2) some hashable auxiliary data
      to be stored in the treedef and to be passed to the ``unflatten_func``.
    unflatten_func: a function taking two arguments: the auxiliary data that was
      returned by ``flatten_func`` and stored in the treedef, and the
      unflattened children. The function should return an instance of
      ``nodetype``.

  See also:
    - :func:`~jax.tree_util.register_static`: simpler API for registering a static pytree.
    - :func:`~jax.tree_util.register_dataclass`: simpler API for registering a dataclass.
    - :func:`~jax.tree_util.register_pytree_with_keys`
    - :func:`~jax.tree_util.register_pytree_node_class`
    - :func:`~jax.tree_util.register_pytree_with_keys_class`

  Example:
    First we'll define a custom type:

    >>> class MyContainer:
    ...   def __init__(self, size):
    ...     self.x = jnp.zeros(size)
    ...     self.y = jnp.ones(size)
    ...     self.size = size

    If we try using this in a JIT-compiled function, we'll get an error because JAX
    does not yet know how to handle this type:

    >>> m = MyContainer(size=5)
    >>> def f(m):
    ...   return m.x + m.y + jnp.arange(m.size)
    >>> jax.jit(f)(m)  # doctest: +IGNORE_EXCEPTION_DETAIL
    Traceback (most recent call last):
      ...
    TypeError: Cannot interpret value of type <class 'jax.tree_util.MyContainer'> as an abstract array; it does not have a dtype attribute

    In order to make our object recognized by JAX, we must register it as
    a pytree:

    >>> def flatten_func(obj):
    ...   children = (obj.x, obj.y)  # children must contain arrays & pytrees
    ...   aux_data = (obj.size,)  # aux_data must contain static, hashable data.
    ...   return (children, aux_data)
    ...
    >>> def unflatten_func(aux_data, children):
    ...   # Here we avoid `__init__` because it has extra logic we don't require:
    ...   obj = object.__new__(MyContainer)
    ...   obj.x, obj.y = children
    ...   obj.size, = aux_data
    ...   return obj
    ...
    >>> jax.tree_util.register_pytree_node(MyContainer, flatten_func, unflatten_func)

    Now with this defined, we can use instances of this type in JIT-compiled functions.

    >>> jax.jit(f)(m)
    Array([1., 2., 3., 4., 5.], dtype=float32)
  """
  default_registry.register_node(nodetype, flatten_func, unflatten_func)
  none_leaf_registry.register_node(nodetype, flatten_func, unflatten_func)
  dispatch_registry.register_node(nodetype, flatten_func, unflatten_func)
  _registry[nodetype] = _RegistryEntry(flatten_func, unflatten_func)


@export
def register_pytree_node_class(cls: Typ) -> Typ:
  """Extends the set of types that are considered internal nodes in pytrees.

  This function is a thin wrapper around ``register_pytree_node``, and provides
  a class-oriented interface.

  Args:
    cls: a type to register as a pytree

  Returns:
    The input class ``cls`` is returned unchanged after being added to JAX's pytree
    registry. This return value allows ``register_pytree_node_class`` to be used as
    a decorator.

  See also:
    - :func:`~jax.tree_util.register_static`: simpler API for registering a static pytree.
    - :func:`~jax.tree_util.register_dataclass`: simpler API for registering a dataclass.
    - :func:`~jax.tree_util.register_pytree_node`
    - :func:`~jax.tree_util.register_pytree_with_keys`
    - :func:`~jax.tree_util.register_pytree_with_keys_class`

  Example:
    Here we'll define a custom container that will be compatible with :func:`jax.jit`
    and other JAX transformations:

    >>> import jax
    >>> @jax.tree_util.register_pytree_node_class
    ... class MyContainer:
    ...   def __init__(self, x, y):
    ...     self.x = x
    ...     self.y = y
    ...   def tree_flatten(self):
    ...     return ((self.x, self.y), None)
    ...   @classmethod
    ...   def tree_unflatten(cls, aux_data, children):
    ...     return cls(*children)
    ...
    >>> m = MyContainer(jnp.zeros(4), jnp.arange(4))
    >>> def f(m):
    ...   return m.x + 2 * m.y
    >>> jax.jit(f)(m)
    Array([0., 2., 4., 6.], dtype=float32)
  """
  register_pytree_node(cls, op.methodcaller("tree_flatten"), cls.tree_unflatten)
  return cls


@export
def tree_map(f: Callable[..., Any],
             tree: Any,
             *rest: Any,
             is_leaf: Callable[[Any], bool] | None = None) -> Any:
  """Maps a multi-input function over pytree args to produce a new pytree.

  Args:
    f: function that takes ``1 + len(rest)`` arguments, to be applied at the
      corresponding leaves of the pytrees.
    tree: a pytree to be mapped over, with each leaf providing the first
      positional argument to ``f``.
    rest: a tuple of pytrees, each of which has the same structure as ``tree``
      or has ``tree`` as a prefix.
    is_leaf: an optionally specified function that will be called at each
      flattening step. It should return a boolean, which indicates whether the
      flattening should traverse the current object, or if it should be stopped
      immediately, with the whole subtree being treated as a leaf.

  Returns:
    A new pytree with the same structure as ``tree`` but with the value at each
    leaf given by ``f(x, *xs)`` where ``x`` is the value at the corresponding
    leaf in ``tree`` and ``xs`` is the tuple of values at corresponding nodes in
    ``rest``.

  Examples:

    >>> import jax.tree_util
    >>> jax.tree_util.tree_map(lambda x: x + 1, {"x": 7, "y": 42})
    {'x': 8, 'y': 43}

    If multiple inputs are passed, the structure of the tree is taken from the
    first input; subsequent inputs need only have ``tree`` as a prefix:

    >>> jax.tree_util.tree_map(lambda x, y: [x] + y, [5, 6], [[7, 9], [1, 2]])
    [[5, 7, 9], [6, 1, 2]]

  See Also:
    - :func:`jax.tree.leaves`
    - :func:`jax.tree.reduce`
  """
  leaves, treedef = tree_flatten(tree, is_leaf)
  all_leaves = [leaves] + [treedef.flatten_up_to(r) for r in rest]
  return treedef.unflatten(f(*xs) for xs in zip(*all_leaves))


@export
def build_tree(treedef: PyTreeDef, xs: Any) -> Any:
  """Build a treedef from a nested iterable structure

  Args:
    treedef: the PyTreeDef structure to build.
    xs: nested iterables matching the arity as the treedef

  Returns:
    object with structure defined by treedef

  See Also:
    - :func:`jax.tree.unflatten`

  Example:
    >>> import jax
    >>> tree = [(1, 2), {'a': 3, 'b': 4}]
    >>> treedef = jax.tree.structure(tree)

    Both ``build_tree`` and :func:`jax.tree_util.tree_unflatten` can reconstruct
    the tree from new values, but ``build_tree`` takes these values in terms of
    a nested rather than flat structure:

    >>> jax.tree_util.build_tree(treedef, [[10, 11], [12, 13]])
    [(10, 11), {'a': 12, 'b': 13}]
    >>> jax.tree_util.tree_unflatten(treedef, [10, 11, 12, 13])
    [(10, 11), {'a': 12, 'b': 13}]
  """
  return treedef.from_iterable_tree(xs)


@export
def tree_transpose(outer_treedef: PyTreeDef, inner_treedef: PyTreeDef | None,
                   pytree_to_transpose: Any) -> Any:
  """Transform a tree having tree structure (outer, inner) into one having structure (inner, outer).

  Args:
    outer_treedef: PyTreeDef representing the outer tree.
    inner_treedef: PyTreeDef representing the inner tree.
      If None, then it will be inferred from outer_treedef and the structure of
      pytree_to_transpose.
    pytree_to_transpose: the pytree to be transposed.

  Returns:
    transposed_pytree: the transposed pytree.

  Examples:
    >>> import jax
    >>> tree = [(1, 2, 3), (4, 5, 6)]
    >>> inner_structure = jax.tree.structure(('*', '*', '*'))
    >>> outer_structure = jax.tree.structure(['*', '*'])
    >>> jax.tree.transpose(outer_structure, inner_structure, tree)
    ([1, 4], [2, 5], [3, 6])

    Inferring the inner structure:

    >>> jax.tree.transpose(outer_structure, None, tree)
    ([1, 4], [2, 5], [3, 6])
  """
  flat, treedef = tree_flatten(pytree_to_transpose)
  if inner_treedef is None:
    inner_treedef = tree_structure(outer_treedef.flatten_up_to(pytree_to_transpose)[0])
  inner_size = inner_treedef.num_leaves
  outer_size = outer_treedef.num_leaves
  if treedef.num_leaves != (inner_size * outer_size):
    expected_treedef = outer_treedef.compose(inner_treedef)
    raise TypeError(f"Mismatch\n{treedef}\n != \n{expected_treedef}")
  iter_flat = iter(flat)
  lol = [
      [next(iter_flat) for _ in range(inner_size)] for __ in range(outer_size)
  ]
  transposed_lol = zip(*lol)
  subtrees = map(partial(tree_unflatten, outer_treedef), transposed_lol)
  return tree_unflatten(inner_treedef, subtrees)


# TODO(mattjj): remove the Python-side registry when the C++-side registry is
# sufficiently queryable that we can express _replace_nones. That may mean once
# we have a flatten_one function.
_RegistryEntry = collections.namedtuple("_RegistryEntry", ["to_iter", "from_iter"])
_registry = {
    tuple: _RegistryEntry(lambda xs: (xs, None), lambda _, xs: tuple(xs)),
    list: _RegistryEntry(lambda xs: (xs, None), lambda _, xs: list(xs)),
    dict: _RegistryEntry(lambda xs: unzip2(sorted(xs.items()))[::-1],
                         lambda keys, xs: dict(zip(keys, xs))),
    type(None): _RegistryEntry(lambda z: ((), None), lambda _, xs: None),
}

def _replace_nones(sentinel, tree):
  """Replaces ``None`` in ``tree`` with ``sentinel``."""
  leaves, treedef = none_leaf_registry.flatten(tree)
  leaves = map(lambda x: sentinel if x is None else x, leaves)
  return treedef.unflatten(leaves)


no_initializer = object()


@overload
def tree_reduce(function: Callable[[T, Any], T],
                tree: Any,
                *,
                is_leaf: Callable[[Any], bool] | None = None) -> T:
    ...


@overload
def tree_reduce(function: Callable[[T, Any], T],
                tree: Any,
                initializer: T,
                is_leaf: Callable[[Any], bool] | None = None) -> T:
    ...


@export
def tree_reduce(function: Callable[[T, Any], T],
                tree: Any,
                initializer: Any = no_initializer,
                is_leaf: Callable[[Any], bool] | None = None) -> T:
  """Call reduce() over the leaves of a tree.

  Args:
    function: the reduction function
    tree: the pytree to reduce over
    initializer: the optional initial value
    is_leaf : an optionally specified function that will be called at each
      flattening step. It should return a boolean, which indicates whether the
      flattening should traverse the current object, or if it should be stopped
      immediately, with the whole subtree being treated as a leaf.

  Returns:
    result: the reduced value.

  Examples:
    >>> import jax
    >>> import operator
    >>> jax.tree.reduce(operator.add, [1, (2, 3), [4, 5, 6]])
    21

  See Also:
    - :func:`jax.tree.leaves`
    - :func:`jax.tree.map`
  """
  if initializer is no_initializer:
    return functools.reduce(function, tree_leaves(tree, is_leaf=is_leaf))
  else:
    return functools.reduce(function, tree_leaves(tree, is_leaf=is_leaf), initializer)


@export
def tree_all(tree: Any) -> bool:
  """Call all() over the leaves of a tree.

  Args:
    tree: the pytree to evaluate

  Returns:
    result: boolean True or False

  Examples:
    >>> import jax
    >>> jax.tree.all([True, {'a': True, 'b': (True, True)}])
    True
    >>> jax.tree.all([False, (True, False)])
    False

  See Also:
    - :func:`jax.tree_util.tree_reduce`
    - :func:`jax.tree_util.tree_leaves`
  """
  return all(tree_leaves(tree))


register_pytree_node(
  collections.OrderedDict,
  lambda x: (tuple(x.values()), tuple(x.keys())),
  lambda keys, values: collections.OrderedDict(safe_zip(keys, values)))

def _flatten_defaultdict(d):
  keys = tuple(sorted(d))
  return tuple(d[k] for k in keys), (d.default_factory, keys)

register_pytree_node(
  collections.defaultdict,
  _flatten_defaultdict,
  lambda s, values: collections.defaultdict(s[0], safe_zip(s[1], values)))  # type: ignore[index,call-overload]


class _HashableCallableShim:
  """Object that delegates __call__, __hash__, and __eq__ to another object."""

  def __init__(self, fun):
    self.fun = fun

  def __call__(self, *args, **kw):
    return self.fun(*args, **kw)

  def __hash__(self):
    return hash(self.fun)

  def __eq__(self, other):
    if isinstance(other, _HashableCallableShim):
      return self.fun == other.fun
    return self.fun == other

  def __repr__(self):
    return f'_HashableCallableShim({self.fun!r})'


@export
class Partial(functools.partial):
  """A version of functools.partial that works in pytrees.

  Use it for partial function evaluation in a way that is compatible with JAX's
  transformations, e.g., ``Partial(func, *args, **kwargs)``.

  (You need to explicitly opt-in to this behavior because we didn't want to give
  functools.partial different semantics than normal function closures.)

  For example, here is a basic usage of ``Partial`` in a manner similar to
  ``functools.partial``:

  >>> import jax.numpy as jnp
  >>> add_one = Partial(jnp.add, 1)
  >>> add_one(2)
  Array(3, dtype=int32, weak_type=True)

  Pytree compatibility means that the resulting partial function can be passed
  as an argument within transformed JAX functions, which is not possible with a
  standard ``functools.partial`` function:

  >>> from jax import jit
  >>> @jit
  ... def call_func(f, *args):
  ...   return f(*args)
  ...
  >>> call_func(add_one, 2)
  Array(3, dtype=int32, weak_type=True)

  Passing zero arguments to ``Partial`` effectively wraps the original function,
  making it a valid argument in JAX transformed functions:

  >>> call_func(Partial(jnp.add), 1, 2)
  Array(3, dtype=int32, weak_type=True)

  Had we passed ``jnp.add`` to ``call_func`` directly, it would have resulted in
  a ``TypeError``.

  Note that if the result of ``Partial`` is used in the context where the
  value is traced, it results in all bound arguments being traced when passed
  to the partially-evaluated function:

  >>> print_zero = Partial(print, 0)
  >>> print_zero()
  0
  >>> call_func(print_zero)  # doctest:+ELLIPSIS
  Traced<ShapedArray(int32[], weak_type=True)>with<DynamicJaxprTrace...>
  """

  def __new__(klass, func, *args, **kw):
    # In Python 3.10+, if func is itself a functools.partial instance,
    # functools.partial.__new__ would merge the arguments of this Partial
    # instance with the arguments of the func. We box func in a class that does
    # not (yet) have a `func` attribute to defeat this optimization, since we
    # care exactly which arguments are considered part of the pytree.
    if isinstance(func, functools.partial):
      original_func = func
      func = _HashableCallableShim(original_func)
      out = super().__new__(klass, func, *args, **kw)
      func.func = original_func.func
      func.args = original_func.args
      func.keywords = original_func.keywords
      return out
    else:
      return super().__new__(klass, func, *args, **kw)


register_pytree_node(
    Partial,
    lambda partial_: ((partial_.args, partial_.keywords), partial_.func),
    lambda func, xs: Partial(func, *xs[0], **xs[1]),  # type: ignore[index]
)


# broadcast_prefix is not exported.
def broadcast_prefix(prefix_tree: Any, full_tree: Any,
                     is_leaf: Callable[[Any], bool] | None = None
                     ) -> list[Any]:
  # If prefix_tree is not a tree prefix of full_tree, this code can raise a
  # ValueError; use prefix_errors to find disagreements and raise more precise
  # error messages.
  result = []
  num_leaves = lambda t: tree_structure(t).num_leaves
  add_leaves = lambda x, subtree: result.extend([x] * num_leaves(subtree))
  tree_map(add_leaves, prefix_tree, full_tree, is_leaf=is_leaf)
  return result


# flatten_one_level is not exported.
def flatten_one_level(pytree: Any) -> tuple[Iterable[Any], Hashable]:
  """Flatten the given pytree node by one level.

  Args:
    pytree: A valid pytree node, either built-in or registered via
      :func:`register_pytree_node` or related functions.

  Returns:
    A pair of the pytrees flattened children and its hashable metadata.

  Raises:
    ValueError: If the given pytree is not a built-in or registered container
    via ``register_pytree_node`` or ``register_pytree_with_keys``.

  Example:
    >>> import jax
    >>> from jax._src.tree_util import flatten_one_level
    >>> flattened, meta = flatten_one_level({'a': [1, 2], 'b': {'c': 3}})
    >>> flattened
    ([1, 2], {'c': 3})
    >>> meta
    ('a', 'b')
  """
  out = default_registry.flatten_one_level(pytree)
  if out is None:
    raise ValueError(f"can't tree-flatten type: {type(pytree)}")
  else:
    return out


# prefix_errors is not exported
def prefix_errors(prefix_tree: Any, full_tree: Any,
                  is_leaf: Callable[[Any], bool] | None = None,
                  ) -> list[Callable[[str], ValueError]]:
  return list(_prefix_error((), prefix_tree, full_tree, is_leaf))


# equality_errors is not exported
def equality_errors(
    tree1: Any, tree2: Any, is_leaf: Callable[[Any], bool] | None = None,
) -> Iterable[tuple[KeyPath, str, str, str]]:
  """Helper to describe structural differences between two pytrees.

  Args:
    tree1, tree2: pytrees to compare.

  Usage:

    raise Exception(
        "Value 1 and value 2 must have the same pytree structure, but they have "
        "the following structural differences:\n" +
        ("\n".join(
           f"   - {keystr(path)} is a {thing1} in value 1 and a {thing2} in "
           f" value 2, so {explanation}.\n"
           for path, thing1, thing2, explanation
           in equality_errors(val1, val2))))
  """
  yield from _equality_errors((), tree1, tree2, is_leaf)

# TODO(mattjj): maybe share some logic with _prefix_error?
def _equality_errors(path, t1, t2, is_leaf):
  # If both are leaves, this isn't a structure equality error.
  if (treedef_is_strict_leaf(tree_structure(t1, is_leaf=is_leaf)) and
      treedef_is_strict_leaf(tree_structure(t2, is_leaf=is_leaf))): return

  # The trees may disagree because they are different types:
  if type(t1) != type(t2):
    yield path, str(type(t1)), str(type(t2)), 'their Python types differ'
    return  # no more errors to find

  # Or they may disagree because their roots have different numbers or keys of
  # children (with special-case handling of list/tuple):
  if isinstance(t1, (list, tuple)):
    assert type(t1) == type(t2)
    if len(t1) != len(t2):
      yield (path,
             f'{type(t1).__name__} of length {len(t1)}',
             f'{type(t2).__name__} of length {len(t2)}',
             'the lengths do not match')
      return  # no more errors to find
  t1_children, t1_meta = flatten_one_level(t1)
  t2_children, t2_meta = flatten_one_level(t2)
  t1_children = tuple(t1_children)
  t2_children = tuple(t2_children)
  t1_keys, t2_keys = _child_keys(t1), _child_keys(t2)
  try:
    diff = ' '.join(repr(k.key) for k in
                    set(t1_keys).symmetric_difference(set(t2_keys)))
  except:
    diff = ''
  if len(t1_children) != len(t2_children):
    yield (path,
           f'{type(t1)} with {len(t1_children)} child'
           f'{"ren" if len(t1_children) > 1 else ""}',
           f'{type(t2)} with {len(t2_children)} child'
           f'{"ren" if len(t2_children) > 1 else ""}',
           'the numbers of children do not match' +
           (diff and f', with the symmetric difference of key sets: {{{diff}}}')
           )
    return  # no more errors to find

  # Or they may disagree if their roots have different pytree metadata:
  if t1_meta != t2_meta:
    yield (path,
           f'{type(t1)} with pytree metadata {t1_meta}',
           f'{type(t2)} with pytree metadata {t2_meta}',
           'the pytree node metadata does not match')
    return  # no more errors to find

  # If the root types and numbers of children agree, there must be a mismatch in
  # a subtree, so recurse:
  assert t1_keys == t2_keys, \
      f"equal pytree nodes gave different tree keys: {t1_keys} and {t2_keys}"
  for k, c1, c2 in zip(t1_keys, t1_children, t2_children):
    yield from _equality_errors((*path, k), c1, c2, is_leaf)


@export
@dataclass(frozen=True)
class SequenceKey():
  """Struct for use with :func:`jax.tree_util.register_pytree_with_keys`."""
  idx: int
  def __str__(self):
    return f'[{self.idx!r}]'


@export
@dataclass(frozen=True)
class DictKey():
  """Struct for use with :func:`jax.tree_util.register_pytree_with_keys`."""
  key: Hashable
  def __str__(self):
    return f'[{self.key!r}]'


@export
@dataclass(frozen=True)
class GetAttrKey():
  """Struct for use with :func:`jax.tree_util.register_pytree_with_keys`."""
  name: str
  def __str__(self):
    return f'.{self.name}'


@export
@dataclass(frozen=True)
class FlattenedIndexKey():
  """Struct for use with :func:`jax.tree_util.register_pytree_with_keys`."""
  key: int
  def __str__(self):
    return f'[<flat index {self.key}>]'

BuiltInKeyEntry = Union[SequenceKey, DictKey, GetAttrKey, FlattenedIndexKey]

KeyEntry = TypeVar("KeyEntry", bound=Hashable)
KeyPath = tuple[KeyEntry, ...]


@export
def keystr(keys: KeyPath):
  """Helper to pretty-print a tuple of keys.

  Args:
    keys: A tuple of ``KeyEntry`` or any class that can be converted to string.

  Returns:
    A string that joins all string representations of the keys.

  Example:
    >>> import jax
    >>> keys = (0, 1, 'a', 'b')
    >>> jax.tree_util.keystr(keys)
    '01ab'
  """
  return ''.join([str(k) for k in keys])


class _RegistryWithKeypathsEntry(NamedTuple):
  flatten_with_keys: Callable[..., Any]
  unflatten_func: Callable[..., Any]


def _register_keypaths(
    ty: type[T], handler: Callable[[T], tuple[KeyEntry, ...]]
) -> None:
  def flatten_with_keys(xs):
    children, treedef = _registry[ty].to_iter(xs)
    return list(zip(handler(xs), children)), treedef
  if ty in _registry:
    _registry_with_keypaths[ty] = _RegistryWithKeypathsEntry(
        flatten_with_keys, _registry[ty].from_iter
    )


_registry_with_keypaths = {}

_register_keypaths(
    tuple, lambda xs: tuple(SequenceKey(i) for i in range(len(xs)))
)
_register_keypaths(
    list, lambda xs: tuple(SequenceKey(i) for i in range(len(xs)))
)
_register_keypaths(dict, lambda xs: tuple(DictKey(k) for k in sorted(xs)))

_register_keypaths(
    collections.defaultdict, lambda x: tuple(DictKey(k) for k in x.keys())
)

_register_keypaths(
    collections.OrderedDict, lambda x: tuple(DictKey(k) for k in x.keys())
)


@export
def register_pytree_with_keys(
    nodetype: type[T],
    flatten_with_keys: Callable[
        [T], tuple[Iterable[tuple[KeyEntry, Any]], _AuxData]
    ],
    unflatten_func: Callable[[_AuxData, Iterable[Any]], T],
    flatten_func: None | (
        Callable[[T], tuple[Iterable[Any], _AuxData]]
    ) = None,
):
  """Extends the set of types that are considered internal nodes in pytrees.

  This is a more powerful alternative to ``register_pytree_node`` that allows
  you to access each pytree leaf's key path when flattening and tree-mapping.

  Args:
    nodetype: a Python type to treat as an internal pytree node.
    flatten_with_keys: a function to be used during flattening, taking a value
      of type ``nodetype`` and returning a pair, with (1) an iterable for tuples
      of each key path and its child, and (2) some hashable auxiliary data to be
      stored in the treedef and to be passed to the ``unflatten_func``.
    unflatten_func: a function taking two arguments: the auxiliary data that was
      returned by ``flatten_func`` and stored in the treedef, and the
      unflattened children. The function should return an instance of
      ``nodetype``.
    flatten_func: an optional function similar to ``flatten_with_keys``, but
      returns only children and auxiliary data. It must return the children
      in the same order as ``flatten_with_keys``, and return the same aux data.
      This argument is optional and only needed for faster traversal when
      calling functions without keys like ``tree_map`` and ``tree_flatten``.

  Example:
    First we'll define a custom type:

    >>> class MyContainer:
    ...   def __init__(self, size):
    ...     self.x = jnp.zeros(size)
    ...     self.y = jnp.ones(size)
    ...     self.size = size

    Now register it using a key-aware flatten function:

    >>> from jax.tree_util import register_pytree_with_keys_class, GetAttrKey
    >>> def flatten_with_keys(obj):
    ...   children = [(GetAttrKey('x'), obj.x),
    ...               (GetAttrKey('y'), obj.y)]  # children must contain arrays & pytrees
    ...   aux_data = (obj.size,)  # aux_data must contain static, hashable data.
    ...   return children, aux_data
    ...
    >>> def unflatten(aux_data, children):
    ...   # Here we avoid `__init__` because it has extra logic we don't require:
    ...   obj = object.__new__(MyContainer)
    ...   obj.x, obj.y = children
    ...   obj.size, = aux_data
    ...   return obj
    ...
    >>> jax.tree_util.register_pytree_node(MyContainer, flatten_with_keys, unflatten)

    Now this can be used with functions like :func:`~jax.tree_util.tree_flatten_with_path`:

    >>> m = MyContainer(4)
    >>> leaves, treedef = jax.tree_util.tree_flatten_with_path(m)
  """
  if not flatten_func:
    def flatten_func_impl(tree):
      key_children, treedef = flatten_with_keys(tree)
      return [c for _, c in key_children], treedef
    flatten_func = flatten_func_impl

  register_pytree_node(nodetype, flatten_func, unflatten_func)
  _registry_with_keypaths[nodetype] = _RegistryWithKeypathsEntry(
      flatten_with_keys, unflatten_func
  )


@export
def register_pytree_with_keys_class(cls: Typ) -> Typ:
  """Extends the set of types that are considered internal nodes in pytrees.

  This function is similar to ``register_pytree_node_class``, but requires a
  class that defines how it could be flattened with keys.

  It is a thin wrapper around ``register_pytree_with_keys``, and
  provides a class-oriented interface:

  Args:
    cls: a type to register as a pytree

  Returns:
    The input class ``cls`` is returned unchanged after being added to JAX's pytree
    registry. This return value allows ``register_pytree_node_class`` to be used as
    a decorator.

  See also:
    - :func:`~jax.tree_util.register_static`: simpler API for registering a static pytree.
    - :func:`~jax.tree_util.register_dataclass`: simpler API for registering a dataclass.
    - :func:`~jax.tree_util.register_pytree_node`
    - :func:`~jax.tree_util.register_pytree_with_keys`
    - :func:`~jax.tree_util.register_pytree_node_class`

  Example:
    >>> from jax.tree_util import register_pytree_with_keys_class, GetAttrKey
    >>> @register_pytree_with_keys_class
    ... class Special:
    ...   def __init__(self, x, y):
    ...     self.x = x
    ...     self.y = y
    ...   def tree_flatten_with_keys(self):
    ...     return (((GetAttrKey('x'), self.x), (GetAttrKey('y'), self.y)), None)
    ...   @classmethod
    ...   def tree_unflatten(cls, aux_data, children):
    ...     return cls(*children)
  """
  flatten_func = (
      op.methodcaller("tree_flatten") if hasattr(cls, "tree_flatten") else None
  )
  register_pytree_with_keys(
      cls, op.methodcaller("tree_flatten_with_keys"), cls.tree_unflatten,
      flatten_func
  )
  return cls


@export
def register_dataclass(
    nodetype: Typ, data_fields: Sequence[str], meta_fields: Sequence[str]
) -> Typ:
  """Extends the set of types that are considered internal nodes in pytrees.

  This differs from ``register_pytree_with_keys_class`` in that the C++
  registries use the optimized C++ dataclass builtin instead of the argument
  functions.

  See :ref:`extending-pytrees` for more information about registering pytrees.

  Args:
    nodetype: a Python type to treat as an internal pytree node. This is assumed
      to have the semantics of a :obj:`~dataclasses.dataclass`: namely, class
      attributes represent the whole of the object state, and can be passed
      as keywords to the class constructor to create a copy of the object.
      All defined attributes should be listed among ``meta_fields`` or ``data_fields``.
    meta_fields: auxiliary data field names. These fields *must* contain static,
      hashable, immutable objects, as these objects are used to generate JIT cache
      keys. In particular, ``meta_fields`` cannot contain :class:`jax.Array` or
      :class:`numpy.ndarray` objects.
    data_fields: data field names. These fields *must* be JAX-compatible objects
      such as arrays (:class:`jax.Array` or :class:`numpy.ndarray`), scalars, or
      pytrees whose leaves are arrays or scalars. Note that ``data_fields`` may be
      ``None``, as this is recognized by JAX as an empty pytree.

  Returns:
    The input class ``nodetype`` is returned unchanged after being added to JAX's
    pytree registry. This return value allows ``register_dataclass`` to be partially
    evaluated and used as a decorator as in the example below.

  Example:
    >>> from dataclasses import dataclass
    >>> from functools import partial
    >>>
    >>> @partial(jax.tree_util.register_dataclass,
    ...          data_fields=['x', 'y'],
    ...          meta_fields=['op'])
    ... @dataclass
    ... class MyStruct:
    ...   x: jax.Array
    ...   y: jax.Array
    ...   op: str
    ...
    >>> m = MyStruct(x=jnp.ones(3), y=jnp.arange(3), op='add')
    >>> m
    MyStruct(x=Array([1., 1., 1.], dtype=float32), y=Array([0, 1, 2], dtype=int32), op='add')

    Now that this class is registered, it can be used with functions in :mod:`jax.tree_util`:

    >>> leaves, treedef = jax.tree.flatten(m)
    >>> leaves
    [Array([1., 1., 1.], dtype=float32), Array([0, 1, 2], dtype=int32)]
    >>> treedef
    PyTreeDef(CustomNode(MyStruct[('add',)], [*, *]))
    >>> jax.tree.unflatten(treedef, leaves)
    MyStruct(x=Array([1., 1., 1.], dtype=float32), y=Array([0, 1, 2], dtype=int32), op='add')

    In particular, this registration allows ``m`` to be passed seamlessly through code
    wrapped in :func:`jax.jit` and other JAX transformations:

    >>> @jax.jit
    ... def compiled_func(m):
    ...   if m.op == 'add':
    ...     return m.x + m.y
    ...   else:
    ...     raise ValueError(f"{m.op=}")
    ...
    >>> compiled_func(m)
    Array([1., 2., 3.], dtype=float32)
  """
  # Store inputs as immutable tuples in this scope, because we close over them
  # for later evaluation. This prevents potentially confusing behavior if the
  # caller were to pass in lists that are later mutated.
  meta_fields = tuple(meta_fields)
  data_fields = tuple(data_fields)

  def flatten_with_keys(x):
    meta = tuple(getattr(x, name) for name in meta_fields)
    data = tuple((GetAttrKey(name), getattr(x, name)) for name in data_fields)
    return data, meta

  def unflatten_func(meta, data):
    meta_args = tuple(zip(meta_fields, meta))
    data_args = tuple(zip(data_fields, data))
    kwargs = dict(meta_args + data_args)
    return nodetype(**kwargs)

  def flatten_func(x):
    meta = tuple(getattr(x, name) for name in meta_fields)
    data = tuple(getattr(x, name) for name in data_fields)
    return data, meta

  default_registry.register_dataclass_node(nodetype, list(data_fields), list(meta_fields))
  none_leaf_registry.register_dataclass_node(nodetype, list(data_fields), list(meta_fields))
  dispatch_registry.register_dataclass_node(nodetype, list(data_fields), list(meta_fields))
  _registry[nodetype] = _RegistryEntry(flatten_func, unflatten_func)
  _registry_with_keypaths[nodetype] = _RegistryWithKeypathsEntry(
      flatten_with_keys, unflatten_func
  )
  return nodetype


@export
def register_static(cls: type[H]) -> type[H]:
  """Registers `cls` as a pytree with no leaves.

  Instances are treated as static by :func:`jax.jit`, :func:`jax.pmap`, etc. This can
  be an alternative to labeling inputs as static using ``jit``'s ``static_argnums``
  and ``static_argnames`` kwargs, ``pmap``'s ``static_broadcasted_argnums``, etc.

  Args:
    cls: type to be registered as static. Must be hashable, as defined in
      https://docs.python.org/3/glossary.html#term-hashable.

  Returns:
    The input class ``cls`` is returned unchanged after being added to JAX's
    pytree registry. This allows ``register_static`` to be used as a decorator.

  Examples:
    >>> import jax
    >>> @jax.tree_util.register_static
    ... class StaticStr(str):
    ...   pass

    This static string can now be used directly in :func:`jax.jit`-compiled
    functions, without marking the variable static using ``static_argnums``:

    >>> @jax.jit
    ... def f(x, y, s):
    ...   return x + y if s == 'add' else x - y
    ...
    >>> f(1, 2, StaticStr('add'))
    Array(3, dtype=int32, weak_type=True)
  """
  flatten = lambda obj: ((), obj)
  unflatten = lambda obj, empty_iter_children: obj
  register_pytree_with_keys(cls, flatten, unflatten)
  return cls


@export
def tree_flatten_with_path(
    tree: Any, is_leaf: Callable[[Any], bool] | None = None
) -> tuple[list[tuple[KeyPath, Any]], PyTreeDef]:
  """Flattens a pytree like ``tree_flatten``, but also returns each leaf's key path.

  Args:
    tree: a pytree to flatten. If it contains a custom type, it must be
      registered with ``register_pytree_with_keys``.
  Returns:
    A pair which the first element is a list of key-leaf pairs, each of
    which contains a leaf and its key path. The second element is a treedef
    representing the structure of the flattened tree.
  """
  _, tree_def = tree_flatten(tree, is_leaf)
  return _generate_key_paths(tree, is_leaf), tree_def


@export
def tree_leaves_with_path(
    tree: Any, is_leaf: Callable[[Any], bool] | None = None
) -> list[tuple[KeyPath, Any]]:
  """Gets the leaves of a pytree like ``tree_leaves`` and returns each leaf's key path.

  Args:
    tree: a pytree. If it contains a custom type, it must be registered with
      ``register_pytree_with_keys``.
  Returns:
    A list of key-leaf pairs, each of which contains a leaf and its key path.

  See Also:
    - :func:`jax.tree_util.tree_leaves`
    - :func:`jax.tree_util.tree_flatten_with_path`
  """
  return _generate_key_paths(tree, is_leaf)


# generate_key_paths is not exported.
def generate_key_paths(
    tree: Any, is_leaf: Callable[[Any], bool] | None = None
) -> list[tuple[KeyPath, Any]]:
  return list(_generate_key_paths_((), tree, is_leaf))
_generate_key_paths = generate_key_paths  # alias for backward compat


# The overall logic should be same as PyTreeDef::FlattenIntoImpl
def _generate_key_paths_(
    key_path: KeyPath,
    tree: Any,
    is_leaf: Callable[[Any], bool] | None = None,
) -> Iterable[tuple[KeyPath, Any]]:
  if is_leaf and is_leaf(tree):
    yield key_path, tree
    return
  key_handler = _registry_with_keypaths.get(type(tree))
  if key_handler:
    key_children, _ = key_handler.flatten_with_keys(tree)
    for k, c in key_children:
      yield from _generate_key_paths_((*key_path, k), c, is_leaf)
    return

  flat = default_registry.flatten_one_level(tree)
  if flat is None:
    yield key_path, tree  # strict leaf type
    return

  if (isinstance(tree, tuple) and hasattr(tree, '_fields') and
      flat[1] == type(tree)):
    # handle namedtuple as a special case, based on heuristic
    key_children = [(GetAttrKey(s), getattr(tree, s)) for s in tree._fields]
    for k, c in key_children:
      yield from _generate_key_paths_((*key_path, k), c, is_leaf)
    return

  for i, c in enumerate(flat[0]):
    k = FlattenedIndexKey(i)
    yield from _generate_key_paths_((*key_path, k), c, is_leaf)


@export
def tree_map_with_path(f: Callable[..., Any],
                       tree: Any, *rest: Any,
                       is_leaf: Callable[[Any], bool] | None = None) -> Any:
  """Maps a multi-input function over pytree key path and args to produce a new pytree.

  This is a more powerful alternative of ``tree_map`` that can take the key path
  of each leaf as input argument as well.

  Args:
    f: function that takes ``2 + len(rest)`` arguments, aka. the key path and
      each corresponding leaves of the pytrees.
    tree: a pytree to be mapped over, with each leaf's key path as the first
      positional argument and the leaf itself as the second argument to ``f``.
    *rest: a tuple of pytrees, each of which has the same structure as ``tree``
      or has ``tree`` as a prefix.

  Returns:
    A new pytree with the same structure as ``tree`` but with the value at each
    leaf given by ``f(kp, x, *xs)`` where ``kp`` is the key path of the leaf at
    the corresponding leaf in ``tree``, ``x`` is the leaf value and ``xs`` is
    the tuple of values at corresponding nodes in ``rest``.

  See Also:
    - :func:`jax.tree_util.tree_map`
    - :func:`jax.tree_util.tree_flatten_with_path`
    - :func:`jax.tree_util.tree_leaves_with_path`
  """

  keypath_leaves, treedef = tree_flatten_with_path(tree, is_leaf)
  keypath_leaves = list(zip(*keypath_leaves))
  all_keypath_leaves = keypath_leaves + [treedef.flatten_up_to(r) for r in rest]
  return treedef.unflatten(f(*xs) for xs in zip(*all_keypath_leaves))


def _child_keys(pytree: Any) -> KeyPath:
  assert not treedef_is_strict_leaf(tree_structure(pytree))
  handler = _registry_with_keypaths.get(type(pytree))
  if handler:
    return tuple(k for k, _ in handler.flatten_with_keys(pytree)[0])
  elif isinstance(pytree, tuple) and hasattr(pytree, '_fields'):
    # handle namedtuple as a special case, based on heuristic
    return tuple(GetAttrKey(s) for s in pytree._fields)
  else:
    num_children = len(treedef_children(tree_structure(pytree)))
    return tuple(FlattenedIndexKey(i) for i in range(num_children))


def _prefix_error(
    key_path: KeyPath,
    prefix_tree: Any,
    full_tree: Any,
    is_leaf: Callable[[Any], bool] | None = None,
) -> Iterable[Callable[[str], ValueError]]:
  # A leaf is a valid prefix of any tree:
  if treedef_is_strict_leaf(tree_structure(prefix_tree, is_leaf=is_leaf)):
    return

  # The subtrees may disagree because their roots are of different types:
  if type(prefix_tree) != type(full_tree):
    yield lambda name: ValueError(
      "pytree structure error: different types at key path\n"
      f"    {{name}}{keystr(key_path)}\n"
      f"At that key path, the prefix pytree {{name}} has a subtree of type\n"
      f"    {type(prefix_tree)}\n"
      f"but at the same key path the full pytree has a subtree of different type\n"
      f"    {type(full_tree)}.".format(name=name))
    return  # don't look for more errors in this subtree

  # Or they may disagree if their roots have different numbers or keys of
  # children. Because both prefix_tree and full_tree have the same type at this
  # point, and because prefix_tree is not a leaf, each can be flattened once:
  prefix_tree_children, prefix_tree_meta = flatten_one_level(prefix_tree)
  full_tree_children, full_tree_meta = flatten_one_level(full_tree)
  prefix_tree_children = tuple(prefix_tree_children)
  full_tree_children = tuple(full_tree_children)
  prefix_tree_keys = _child_keys(prefix_tree)
  full_tree_keys = _child_keys(full_tree)
  # First we check special case types (list and tuple, though if they were
  # pytrees we could check strings and sets here, basically Sequences) so that
  # we can report length disagreement rather than integer keys:
  if isinstance(prefix_tree, (list, tuple)):
    if len(prefix_tree) != len(full_tree):
      ty = type(prefix_tree)
      yield lambda name: ValueError(
          f"pytree structure error: different lengths of {ty.__name__} at key path\n"
          f"    {{name}}{keystr(key_path)}\n"
          f"At that key path, the prefix pytree {{name}} has a subtree of type "
          f"{ty.__name__} of length {len(prefix_tree)}, but the full pytree "
          f"has a subtree of the same type but of length {len(full_tree)}."
          .format(name=name))
      return  # don't look for more errors in this subtree
  else:
    # Next we handle the general case of checking child keys.
    try:
      diff = set(prefix_tree_keys).symmetric_difference(set(full_tree_keys))
    except:
      diff = None
    if len(prefix_tree_children) != len(full_tree_children):
      yield lambda name: ValueError(
        "pytree structure error: different numbers of pytree children at key path\n"
        f"    {{name}}{keystr(key_path)}\n"
        f"At that key path, the prefix pytree {{name}} has a subtree of type\n"
        f"    {type(prefix_tree)}\n"
        f"with {len(prefix_tree_children)} child keys\n"
        f"    {' '.join(str(k.key) for k in prefix_tree_keys)}\n"
        f"but at the same key path the full pytree has a subtree of the same "
        f"type but with {len(full_tree_children)} child keys\n"
        f"    {' '.join(str(k.key) for k in full_tree_keys)}\n"
        .format(name=name)
        + ("" if diff is None else
           f"so the symmetric difference on key sets is\n"
           f"    {' '.join(str(k.key) for k in diff)}"))
      return  # don't look for more errors in this subtree

  # Or they may disagree if their roots have different pytree metadata:
  if prefix_tree_meta != full_tree_meta:
    prefix_tree_meta_str = str(prefix_tree_meta)
    full_tree_meta_str = str(full_tree_meta)
    metadata_diff = textwrap.indent(
        "\n".join(
            difflib.ndiff(prefix_tree_meta_str.splitlines(),
                          full_tree_meta_str.splitlines())),
        prefix="    ")
    yield lambda name: ValueError(
      "pytree structure error: different pytree metadata at key path\n"
      f"    {{name}}{keystr(key_path)}\n"
      f"At that key path, the prefix pytree {{name}} has a subtree of type\n"
      f"    {type(prefix_tree)}\n"
      f"with metadata\n"
      f"    {prefix_tree_meta_str}\n"
      f"but at the same key path the full pytree has a subtree of the same "
      f"type but with metadata\n"
      f"    {full_tree_meta_str}\n"
      f"so the diff in the metadata at these pytree nodes is\n"
      f"{metadata_diff}".format(name=name))
    return  # don't look for more errors in this subtree

  # If the root types and numbers of children agree, there must be an error
  # in a subtree, so recurse:
  assert prefix_tree_keys == full_tree_keys, \
    ("equal pytree nodes gave differing prefix_tree_keys: "
     f"{prefix_tree_keys} and {full_tree_keys}")
  for k, t1, t2 in zip(prefix_tree_keys, prefix_tree_children, full_tree_children):
    yield from _prefix_error((*key_path, k), t1, t2)
