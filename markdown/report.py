from cida.application.generate_report import ReportGeneratorUsecase
from cida.infrastructure.filesystem import PhysicalFilesystem
from cida.infrastructure.json_codec import JsonCodec

class ReportGenerator:
    def __init__(self):
        self._usecase = ReportGeneratorUsecase(PhysicalFilesystem(), JsonCodec())

    @property
    def entries(self):
        return self._usecase.entries

    def add_entry(self, filepath, profile, tokens_orig, tokens_base, tokens_new,
                  dict_included, tokens_sidecar, tokens_aux, accepted_transforms, rejected_transforms,
                  semantic_status, execution_time):
        self._usecase.add_entry(filepath, profile, tokens_orig, tokens_base, tokens_new,
                                dict_included, tokens_sidecar, tokens_aux, accepted_transforms,
                                rejected_transforms, semantic_status, execution_time)

    def make_deterministic(self, src_abs):
        self._usecase.make_deterministic(src_abs)

    def generate_markdown(self, deterministic=False):
        return self._usecase.generate_markdown(deterministic)

    def save_reports(self, text_path, json_path, src_abs, report_format="both"):
        self._usecase.save_reports(text_path, json_path, src_abs, report_format)
