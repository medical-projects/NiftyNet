# -*- coding: utf-8 -*-
"""
This module maintains the image partition tables
for images kept on disc.
"""
from __future__ import absolute_import, division, print_function

import os
import shutil

import pandas
import tensorflow as tf  # to use the system level logging

from niftynet.engine.signal import ALL, INFER, TRAIN, VALID
from niftynet.io.image_sets_partitioner import (
    COLUMN_PHASE, COLUMN_UNIQ_ID, SUPPORTED_PHASES, BaseImageSetsPartitioner)
from niftynet.utilities.decorators import singleton
from niftynet.utilities.filename_matching import KeywordsMatching
from niftynet.utilities.niftynet_global_config import NiftyNetGlobalConfig
from niftynet.utilities.util_common import look_up_operations
from niftynet.utilities.util_csv import (match_and_write_filenames_to_csv,
                                         write_csv)


@singleton
class FileImageSetsPartitioner(BaseImageSetsPartitioner):
    """
    This class maintains a pandas.dataframe of filenames for all input sections

    The list of filenames are obtained by searching the specified folders
    or loading from an existing csv file.

    Users can query a subset of the dataframe by train/valid/infer partition
    label and input section names.
    """

    # dataframe (table) of file names in a shape of subject x modality
    _file_list = None
    # dataframes of subject_id:phase_id
    _partition_ids = None

    new_partition = False

    # for saving the splitting index
    data_split_file = ""
    # default parent folder location for searching the image files
    default_image_file_location = \
        NiftyNetGlobalConfig().get_niftynet_home_folder()

    def initialise(self,
                   data_param,
                   new_partition=False,
                   data_split_file=None,
                   ratios=None):
        super(FileImageSetsPartitioner, self).initialise(
            data_param, new_partition, data_split_file, ratios)

        if data_split_file is None:
            self.data_split_file = os.path.join('.', 'dataset_split.csv')
        else:
            self.data_split_file = data_split_file

        self._file_list = None
        self._partition_ids = None

        self.load_data_sections_by_subject()
        self.new_partition = new_partition
        self.randomly_split_dataset(overwrite=new_partition)
        tf.logging.info(self)
        return self

    def num_subjects(self, phase=ALL):
        if self._file_list is None:
            return 0
        phase = self._look_up_phase(phase)

        if phase == ALL:
            return self._file_list[COLUMN_UNIQ_ID].count()
        if self._partition_ids is None:
            return 0
        selector = self._partition_ids[COLUMN_PHASE] == phase
        return self._partition_ids[selector].count()[COLUMN_UNIQ_ID]

    def get_file_list(self, phase=ALL, *section_names):
        """
        get file names as a dataframe, by partitioning phase and section names
        set phase to ALL to load all subsets.

        :param phase: the label of the subset generated by self._partition_ids
                    should be one of the SUPPORTED_PHASES
        :param section_names: one or multiple input section names
        :return: a pandas.dataframe of file names
        """
        if self._file_list is None:
            tf.logging.warning('Empty file list, please initialise'
                               'ImageSetsPartitioner first.')
            return []
        try:
            phase = look_up_operations(phase.lower(), SUPPORTED_PHASES)
        except (ValueError, AttributeError):
            tf.logging.fatal('Unknown phase argument.')
            raise

        for name in section_names:
            try:
                look_up_operations(name, set(self._file_list))
            except ValueError:
                tf.logging.fatal(
                    'Requesting files under input section [%s],\n'
                    'however the section does not exist in the config.', name)
                raise
        if phase == ALL:
            self._file_list = self._file_list.sort_values(COLUMN_UNIQ_ID)
            if section_names:
                section_names = [COLUMN_UNIQ_ID] + list(section_names)
                return self._file_list[section_names]
            return self._file_list
        if self._partition_ids is None or self._partition_ids.empty:
            tf.logging.fatal('No partition ids available.')
            if self.new_partition:
                tf.logging.fatal(
                    'Unable to create new partitions,'
                    'splitting ratios: %s, writing file %s', self.ratios,
                    self.data_split_file)
            elif os.path.isfile(self.data_split_file):
                tf.logging.fatal(
                    'Unable to load %s, initialise the'
                    'ImageSetsPartitioner with `new_partition=True`'
                    'to overwrite the file.', self.data_split_file)
            raise ValueError

        selector = self._partition_ids[COLUMN_PHASE] == phase
        selected = self._partition_ids[selector][[COLUMN_UNIQ_ID]]
        if selected.empty:
            tf.logging.warning(
                'Empty subset for phase [%s], returning None as file list. '
                'Please adjust splitting fractions.', phase)
            return None
        subset = pandas.merge(
            self._file_list, selected, on=COLUMN_UNIQ_ID, sort=True)
        if subset.empty:
            tf.logging.warning(
                'No subject id matched in between file names and '
                'partition files.\nPlease check the partition files %s,\nor '
                'removing it to generate a new file automatically.',
                self.data_split_file)
        if section_names:
            section_names = [COLUMN_UNIQ_ID] + list(section_names)
            return subset[section_names]
        return subset

    def load_data_sections_by_subject(self):
        """
        Go through all input data sections, converting each section
        to a list of file names.

        These lists are merged on ``COLUMN_UNIQ_ID``.

        This function sets ``self._file_list``.
        """
        if not self.data_param:
            tf.logging.fatal(
                'Nothing to load, please check input sections in the config.')
            raise ValueError
        self._file_list = None
        for section_name in self.data_param:
            modality_file_list = self.grep_files_by_data_section(section_name)
            if self._file_list is None:
                # adding all rows of the first modality
                self._file_list = modality_file_list
                continue
            n_rows = self._file_list[COLUMN_UNIQ_ID].count()
            self._file_list = pandas.merge(
                self._file_list,
                modality_file_list,
                how='outer',
                on=COLUMN_UNIQ_ID)
            if self._file_list[COLUMN_UNIQ_ID].count() < n_rows:
                tf.logging.warning('rows not matched in section [%s]',
                                   section_name)

        if self._file_list is None or self._file_list.size == 0:
            tf.logging.fatal(
                "Empty filename lists, please check the csv "
                "files (removing csv_file keyword if it is in the config file "
                "to automatically search folders and generate new csv "
                "files again).\n\n"
                "Please note in the matched file names, each subject id are "
                "created by removing all keywords listed `filename_contains` "
                "in the config.\n"
                "E.g., `filename_contains=foo, bar` will match file "
                "foo_subject42_bar.nii.gz, and the subject id is "
                "_subject42_.\n\n")
            raise IOError

    def grep_files_by_data_section(self, modality_name):
        """
        list all files by a given input data section::
            if the ``csv_file`` property of ``data_param[modality_name]``
            corresponds to a file, read the list from the file;
            otherwise
                write the list to ``csv_file``.

        :return: a table with two columns,
                 the column names are ``(COLUMN_UNIQ_ID, modality_name)``.
        """
        if modality_name not in self.data_param:
            tf.logging.fatal(
                'unknown section name [%s], '
                'current input section names: %s.', modality_name,
                list(self.data_param))
            raise ValueError

        # input data section must have a ``csv_file`` section for loading
        # or writing filename lists
        if isinstance(self.data_param[modality_name], dict):
            mod_spec = self.data_param[modality_name]
        else:
            mod_spec = vars(self.data_param[modality_name])

        #########################
        # guess the csv_file path
        #########################
        temp_csv_file = None
        try:
            csv_file = os.path.expanduser(mod_spec.get('csv_file', None))
            if not os.path.isfile(csv_file):
                # writing to the same folder as data_split_file
                default_csv_file = os.path.join(
                    os.path.dirname(self.data_split_file),
                    '{}.csv'.format(modality_name))
                tf.logging.info(
                    '`csv_file = %s` not found, '
                    'writing to "%s" instead.', csv_file, default_csv_file)
                csv_file = default_csv_file
                if os.path.isfile(csv_file):
                    tf.logging.info('Overwriting existing: "%s".', csv_file)
            csv_file = os.path.abspath(csv_file)
        except (AttributeError, KeyError, TypeError):
            tf.logging.debug('`csv_file` not specified, writing the list of '
                             'filenames to a temporary file.')
            import tempfile
            temp_csv_file = os.path.join(tempfile.mkdtemp(),
                                         '{}.csv'.format(modality_name))
            csv_file = temp_csv_file

        #############################################
        # writing csv file if path_to_search specified
        ##############################################
        if mod_spec.get('path_to_search', None):
            if not temp_csv_file:
                tf.logging.info(
                    '[%s] search file folders, writing csv file %s',
                    modality_name, csv_file)
            # grep files by section properties and write csv
            try:
                matcher = KeywordsMatching.from_dict(
                    input_dict=mod_spec,
                    default_folder=self.default_image_file_location)
                match_and_write_filenames_to_csv([matcher], csv_file)
            except (IOError, ValueError) as reading_error:
                tf.logging.warning(
                    'Ignoring input section: [%s], '
                    'due to the following error:', modality_name)
                tf.logging.warning(repr(reading_error))
                return pandas.DataFrame(
                    columns=[COLUMN_UNIQ_ID, modality_name])
        else:
            tf.logging.info(
                '[%s] using existing csv file %s, skipped filenames search',
                modality_name, csv_file)

        if not os.path.isfile(csv_file):
            tf.logging.fatal('[%s] csv file %s not found.', modality_name,
                             csv_file)
            raise IOError
        ###############################
        # loading the file as dataframe
        ###############################
        try:
            csv_list = pandas.read_csv(
                csv_file,
                header=None,
                dtype=(str, str),
                names=[COLUMN_UNIQ_ID, modality_name],
                skipinitialspace=True)
        except Exception as csv_error:
            tf.logging.fatal(repr(csv_error))
            raise

        if temp_csv_file:
            shutil.rmtree(os.path.dirname(temp_csv_file), ignore_errors=True)

        return csv_list

    # pylint: disable=broad-except
    def randomly_split_dataset(self, overwrite=False):
        """
        Label each subject as one of the ``TRAIN``, ``VALID``, ``INFER``,
        use ``self.ratios`` to compute the size of each set.

        The results will be written to ``self.data_split_file`` if overwrite
        otherwise it tries to read partition labels from it.

        This function sets ``self._partition_ids``.
        """
        if overwrite:
            phases = self._create_partitions()
            write_csv(self.data_split_file,
                      zip(self._file_list[COLUMN_UNIQ_ID], phases))
        elif os.path.isfile(self.data_split_file):
            tf.logging.warning(
                'Loading from existing partitioning file %s, '
                'ignoring partitioning ratios.', self.data_split_file)

        if os.path.isfile(self.data_split_file):
            try:
                self._partition_ids = pandas.read_csv(
                    self.data_split_file,
                    header=None,
                    dtype=(str, str),
                    names=[COLUMN_UNIQ_ID, COLUMN_PHASE],
                    skipinitialspace=True)
                assert not self._partition_ids.empty, \
                    "partition file is empty."
            except Exception as csv_error:
                tf.logging.warning(
                    "Unable to load the existing partition file %s, %s",
                    self.data_split_file, repr(csv_error))
                self._partition_ids = None

            try:
                phase_strings = self._partition_ids[COLUMN_PHASE]
                phase_strings = phase_strings.astype(str).str.lower()
                is_valid_phase = phase_strings.isin(SUPPORTED_PHASES)
                assert is_valid_phase.all(), \
                    "Partition file contains unknown phase id."
                self._partition_ids[COLUMN_PHASE] = phase_strings
            except (TypeError, AssertionError):
                tf.logging.warning(
                    'Please make sure the values of the second column '
                    'of data splitting file %s, in the set of phases: %s.\n'
                    'Remove %s to generate random data partition file.',
                    self.data_split_file, SUPPORTED_PHASES,
                    self.data_split_file)
                raise ValueError

    def __str__(self):
        return self.to_string()

    def to_string(self):
        """
        Print summary of the partitioner.
        """
        n_subjects = self.num_subjects()
        summary_str = '\n\nNumber of subjects {}, '.format(n_subjects)
        if self._file_list is not None:
            summary_str += 'input section names: {}\n'.format(
                list(self._file_list))
        if self._partition_ids is not None and n_subjects > 0:
            n_train = self.num_subjects(TRAIN)
            n_valid = self.num_subjects(VALID)
            n_infer = self.num_subjects(INFER)
            summary_str += \
                'Dataset partitioning:\n' \
                '-- {} {} cases ({:.2f}%),\n' \
                '-- {} {} cases ({:.2f}%),\n' \
                '-- {} {} cases ({:.2f}%).\n'.format(
                    TRAIN, n_train, float(n_train) / float(n_subjects) * 100.0,
                    VALID, n_valid, float(n_valid) / float(n_subjects) * 100.0,
                    INFER, n_infer, float(n_infer) / float(n_subjects) * 100.0)
        else:
            summary_str += '-- using all subjects ' \
                           '(without data partitioning).\n'
        return summary_str

    def has_phase(self, phase):
        """

        :return: True if the `phase` subset of images is not empty.
        """
        if self._partition_ids is None or self._partition_ids.empty:
            return False
        selector = self._partition_ids[COLUMN_PHASE] == phase
        if not selector.any():
            return False
        selected = self._partition_ids[selector][[COLUMN_UNIQ_ID]]
        subset = pandas.merge(
            left=self._file_list,
            right=selected,
            on=COLUMN_UNIQ_ID,
            sort=False)
        return not subset.empty

    @property
    def validation_files(self):
        """

        :return: the list of validation filenames.
        """
        if self.has_validation:
            return self.get_file_list(VALID)
        return self.all_files

    @property
    def train_files(self):
        """

        :return: the list of training filenames.
        """
        if self.has_training:
            return self.get_file_list(TRAIN)
        return self.all_files

    @property
    def inference_files(self):
        """

        :return: the list of inference filenames
            (defaulting to list of all filenames if no partition definition)
        """
        if self.has_inference:
            return self.get_file_list(INFER)
        return self.all_files

    @property
    def all_files(self):
        return self.get_file_list()

    def get_image_lists_by(self, phase=None, action='train'):
        """
        Get file lists by action and phase.

        This function returns file lists for training/validation/inference
        based on the phase or action specified by the user.

        ``phase`` has a higher priority:
        If `phase` specified, the function returns the corresponding
        file list (as a list).

        otherwise, the function checks ``action``:
        it returns train and validation file lists if it's training action,
        otherwise returns inference file list.

        :param action: an action
        :param phase: an element from ``{TRAIN, VALID, INFER, ALL}``
        :return:
        """
        if phase:
            try:
                return [self.get_file_list(phase=phase)]
            except (ValueError, AttributeError):
                tf.logging.warning('phase `parameter` %s ignored', phase)

        if action and TRAIN.startswith(action):
            file_lists = [self.train_files]
            if self.has_validation:
                file_lists.append(self.validation_files)
            return file_lists

        return [self.inference_files]

    def reset(self):
        super(FileImageSetsPartitioner, self).reset()

        self._file_list = None
        self._partition_ids = None
        self.new_partition = False

        self.data_split_file = ""
        self.default_image_file_location = \
            NiftyNetGlobalConfig().get_niftynet_home_folder()
