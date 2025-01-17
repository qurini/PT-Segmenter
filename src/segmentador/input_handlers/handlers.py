
"""Handle different segmenter input types."""
import typing as t
import abc
import warnings
import collections

import transformers
import torch
import numpy as np
import regex

try:
    import datasets

except ImportError:
    pass

try:
    import pandas as pd

except ImportError:
    pass


InputHandlerOutputType = t.Tuple[transformers.BatchEncoding, t.Optional[t.List[str]], int]


class _BaseInputHandler(abc.ABC):
    """Base class for Segmenter Input Handling."""


class InputHandlerString(_BaseInputHandler):
    """Handle string as segmenter input."""

    RE_BLANK_SPACES = regex.compile(r"\s+")
    RE_JUSTIFICATIVA = regex.compile(
        "|".join(
            (
                r"\s*".join("JUSTIFICATIVA"),
                r"\s*".join([*"JUSTIFICA", "[CÇ]", "[AÁÀÃÃ]", "O"]),
                r"\s*".join("ANEXOS") + "?",
            )
        )
    )

    @classmethod
    def setup_regex_justificativa(
        cls,
        regex_justificativa: t.Optional[t.Union[str, regex.Pattern]] = None,
    ) -> regex.Pattern:
        """Compile or set default 'JUSTIFICATIVA' block regex.

        If the provided regex is already compiled, then this function will return its own
        argument.
        """
        if regex_justificativa is None:
            regex_justificativa = cls.RE_JUSTIFICATIVA

        if isinstance(regex_justificativa, str):
            regex_justificativa = regex.compile(f"(?:{regex_justificativa})")

        return regex_justificativa

    @classmethod
    def preprocess_legal_text(
        cls,
        text: str,
        regex_justificativa: t.Optional[t.Union[str, regex.Pattern]] = None,
    ) -> t.Tuple[str, t.List[str]]:
        """Apply minimal legal text preprocessing.

        The preprocessing steps are:
        1. Coalesce all blank spaces in text;
        2. Remove all trailing and leading blank spaces; and
        3. Pre-segment text into legal text content and `justificativa`.

        Parameters
        ----------
        text : str
            Text to be preprocessed.

        regex_justificativa : str, regex.Pattern or None, default=None
            Regular expression specifying how the `justificativa` portion from legal
            documents should be detected. If None, will use the pattern predefined in
            `Segmenter.RE_JUSTIFICATIVA` class attribute.

        Returns
        -------
        preprocessed_text : str
            Content from `text` after the preprocessing steps.

        justificativa_block : t.List[str]
            Detected legal text `justificativa` blocks.
        """
        text = cls.RE_BLANK_SPACES.sub(" ", text)
        text = text.strip()

        regex_justificativa = cls.setup_regex_justificativa(regex_justificativa)
        text, *justificativa = regex_justificativa.split(text)

        return text, justificativa

    @classmethod
    def tokenize(
        cls,
        text: str,
        tokenizer: transformers.BertTokenizerFast,
        *args: t.Any,
        regex_justificativa: t.Optional[t.Union[str, regex.Pattern]] = None,
        **kwargs: t.Any,
    ) -> InputHandlerOutputType:
        """Split a string into tokens.

        The returned value is the `tokenizer` output casted as key-tensor pairs
        in Pytorch format.

        Parameters
        ----------
        text : str
            Input string to be tokenized.

        tokenizer : transformers.BertTokenizerFast
            Tokenizer used to split `text` into tokens.

        *args : tuple, optional
            Ignored.

        regex_justificativa : str or regex.Pattern or None, default=None
            Regular expression to detect `justificativa` blocks. If `None`,
            then `InputHandlerString.RE_JUSTIFICATIVA` is used by default.

        **kwargs : dict, optional
            Ignored.

        Returns
        -------
        tokens : transformers.BatchEncoding
            Input `text` split into tokens by `tokenizer`.

        justificativa : t.List[str] or None
            Detected `justificativa` blocks by `regex_justificativa`.

        num_tokens : int
            Total length of `tokens`.
        """
        # pylint: disable='unused-argument'
        text, justificativa = cls.preprocess_legal_text(
            text,
            regex_justificativa=regex_justificativa,
        )

        tokens = tokenizer(
            text,
            padding=False,
            truncation=False,
            return_tensors="pt",
            return_length=True,
        )

        num_tokens = tokens.pop("length")

        return tokens, justificativa, num_tokens


class InputHandlerMapping(_BaseInputHandler):
    """Reformat input as a generic mapping into the intended segmenter input format."""

    @classmethod
    def _val_to_tensor(cls, val: t.Any) -> torch.Tensor:
        if torch.is_tensor(val):
            return val  # type: ignore

        if isinstance(val, np.ndarray):
            return torch.from_numpy(val)

        try:
            ret = torch.tensor(val)

        except ValueError:
            ret = torch.from_numpy(np.concatenate(val))

        return ret

    @classmethod
    def tokenize(
        cls,
        text: t.MutableMapping[str, t.Sequence[int]],
        *args: t.Any,
        **kwargs: t.Any,
    ) -> InputHandlerOutputType:
        """Reformat a generic mapping into key-tensor pairs (Pytorch format).

        The entire mapping is interpreted as a single document. If this is not
        the intended behaviour, you must feed separated documents into the
        Segmenter model.

        Parameters
        ----------
        text : t.Dict[str, t.List[int]]
            Mapping of key to input ids.

        *args : tuple, optional
            Ignored.

        **kwargs : dict, optional
            Ignored.

        Returns
        -------
        tokens : transformers.BatchEncoding
            Input `text` split into tokens by `tokenizer`.

        justificativa : None
            Returned just to keep consistency with InputHandler* API.

        num_tokens : int
            Total length of `tokens`.
        """
        # pylint: disable='unused-argument'
        tokens = transformers.BatchEncoding(
            {key: cls._val_to_tensor(val) for key, val in text.items()}
        )
        justificativa = None
        num_tokens = int(tokens["input_ids"].numel())

        return tokens, justificativa, num_tokens


class InputHandlerDataset(_BaseInputHandler):
    """Reformat the input as a huggingface Dataset into the segmenter input format."""

    @classmethod
    def tokenize(cls, text: t.Any, *args: t.Any, **kwargs: t.Any) -> InputHandlerOutputType:
        """Reformat a huggingface Dataset into key-tensor pairs (Pytorch format).

        The entire mapping is interpreted as a single document. If this is not
        the intended behaviour, you must feed separated documents into the
        Segmenter model.

        Parameters
        ----------
        text : datasets.Dataset
            Huggingface Dataset to be reformatted.

        *args : tuple, optional
            Ignored.

        **kwargs : dict, optional
            Ignored.

        Returns
        -------
        tokens : transformers.BatchEncoding
            Input `text` split into tokens by `tokenizer`.

        justificativa : None
            Returned just to keep consistency with InputHandler* API.

        num_tokens : int
            Total length of `tokens`.
        """
        # pylint: disable='unused-argument'
        return InputHandlerMapping.tokenize(text.to_dict())


def tokenize_input(text: t.Any, *args: t.Any, **kwargs: t.Any) -> InputHandlerOutputType:
    """Reformat `text` into the segmenter intended input format.

    The correct strategy is chosen automatically by this method, if possible.
    The supported formats are string (`str`), huggingface Datasets (`datasets.Dataset`),
    or a generic key-value mapping (such as `dict` or `tran.
    """
    if isinstance(text, str):
        return InputHandlerString.tokenize(text, *args, **kwargs)

    try:
        is_hugginface_dataset = isinstance(text, datasets.Dataset)

    except NameError:
        is_hugginface_dataset = False

    if is_hugginface_dataset:
        return InputHandlerDataset.tokenize(text, *args, **kwargs)

    try:
        is_pandas_dataframe = isinstance(text, pd.DataFrame)
        is_pandas_series = isinstance(text, pd.Series)

    except NameError:
        is_pandas_dataframe = False
        is_pandas_series = False

    if is_pandas_dataframe:
        raise TypeError(
            "pandas.DataFrame as input is not supported, as it is ambigous which "
            "column should be used. Please provide the input as a pandas.Series or "
            "a list."
        )

    if is_pandas_series:
        text = text.tolist()

    if hasattr(text, "items"):
        return InputHandlerMapping.tokenize(text, *args, **kwargs)

    if isinstance(text, collections.abc.Iterable):
        warnings.warn(
            message=(
                "Provided input is an iterable, which will be concatenated into a single text. "
                "If the intended behaviour is to feed multiple, independent documents, please "
                "provide them separately."
            ),
            category=UserWarning,
        )
        return InputHandlerString.tokenize("\n".join(text), *args, **kwargs)

    raise TypeError(
        f"Unrecognized 'text' type: {type(text)}. Please cast your text input to "
        "a string, datasets.Dataset, a valid key-value mapping, or a generic iterable."
    )